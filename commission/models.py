"""模块：分佣 SAAS 数据模型
SAAS 级分佣引擎——事件驱动、规则外置、参与方可配置。
子服务只管说"发生了什么"，SAAS 负责"怎么分"。"""

import uuid
from django.db import models
from django.utils import timezone as _timezone
from gvsdsdk.model_base import QModel
from gvsdsdk.models import UUIDField


class CommissionEvent(QModel):
    """分佣事件表——子服务触发的分佣事件

    子服务在业务发生时（订单完成、会员购买、充值到账等）
    调用 CommissionEngine.Dispatch() 写入此表。
    """

    EVT_ORDER_COMPLETE = 'order_complete'
    EVT_MEMBER_PURCHASE = 'member_purchase'
    EVT_RECHARGE = 'recharge'
    EVT_PENALTY = 'penalty'
    EVT_REGISTER = 'register'
    EVT_CUSTOM = 'custom'

    EVT_TYPE_CHOICES = [
        (EVT_ORDER_COMPLETE, '订单完成'),
        (EVT_MEMBER_PURCHASE, '会员购买'),
        (EVT_RECHARGE, '充值到账'),
        (EVT_PENALTY, '罚款缴纳'),
        (EVT_REGISTER, '用户注册'),
        (EVT_CUSTOM, '自定义事件'),
    ]

    EVT_PENDING = 0
    EVT_PROCESSING = 1
    EVT_SETTLED = 2
    EVT_FAILED = 3

    EVT_STATUS_CHOICES = [
        (EVT_PENDING, '待处理'),
        (EVT_PROCESSING, '处理中'),
        (EVT_SETTLED, '已结算'),
        (EVT_FAILED, '结算失败'),
    ]

    EventUUID = UUIDField(primary_key=True)
    TenantUUID = UUIDField()
    EventType = models.CharField(max_length=32, choices=EVT_TYPE_CHOICES)
    EventKey = models.CharField(max_length=128)
    TotalAmount = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    SourceUserUUID = UUIDField(null=True, blank=True)
    EventPayload = models.JSONField(default=dict)
    Participants = models.JSONField(default=dict)
    EventStatus = models.SmallIntegerField(default=EVT_PENDING, choices=EVT_STATUS_CHOICES)
    EventTime = models.DateTimeField(default=_timezone.now)
    ProcessTime = models.DateTimeField(null=True, blank=True)
    CreateTime = models.DateTimeField(default=_timezone.now)

    class Meta:
        db_table = 'CommissionEvent'
        app_label = 'gvsdsdk'
        verbose_name = '分佣事件'
        verbose_name_plural = '分佣事件'
        unique_together = [('TenantUUID', 'EventKey')]
        managed = False
        # 索引由业务侧（gvsds）迁移声明；SDK 为 managed=False 镜像，不重复声明（避免 models.E030）

    def __str__(self):
        return f'{self.EventType}:{self.EventKey}'

    @property
    def IsSettled(self):
        return self.EventStatus == self.EVT_SETTLED

    def MarkProcessing(self):
        self.EventStatus = self.EVT_PROCESSING
        self.save(update_fields=['EventStatus'])

    def MarkSettled(self):
        self.EventStatus = self.EVT_SETTLED
        self.ProcessTime = _timezone.now()
        self.save(update_fields=['EventStatus', 'ProcessTime'])

    def MarkFailed(self):
        self.EventStatus = self.EVT_FAILED
        self.ProcessTime = _timezone.now()
        self.save(update_fields=['EventStatus', 'ProcessTime'])


class RateTable(QModel):
    """费率表——可复用的费率配置

    每个租户可创建多张费率表，不同事件类型绑定不同的表。
    例如：打手分成费率表、提现手续费率表、罚款分红费率表。
    """

    RateTableUUID = UUIDField(primary_key=True)
    TenantUUID = UUIDField()
    TableCode = models.CharField(max_length=64)
    TableName = models.CharField(max_length=128)
    TableDescription = models.CharField(max_length=512, default='')
    TableStatus = models.SmallIntegerField(default=1)
    CreateTime = models.DateTimeField(default=_timezone.now)
    UpdateTime = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'CommissionRateTable'
        app_label = 'gvsdsdk'
        verbose_name = '费率表'
        verbose_name_plural = '费率表'
        unique_together = [('TenantUUID', 'TableCode')]
        managed = False
        # 索引由业务侧（gvsds）迁移声明；SDK 为 managed=False 镜像，不重复声明（避免 models.E030）

    def __str__(self):
        return f'{self.TableCode}:{self.TableName}'

    def GetRate(self, key):
        try:
            entry = self.Entries.get(RateKey=key, EntryStatus=1)
            return entry.RateValue
        except RateEntry.DoesNotExist:
            return None

    def GetRates(self):
        return {
            e.RateKey: e.RateValue
            for e in self.Entries.filter(EntryStatus=1)
        }


class RateEntry(QModel):
    """费率条目——费率表中的一行"""

    EntryUUID = UUIDField(primary_key=True)
    Table = models.ForeignKey(
        RateTable, on_delete=models.CASCADE,
        related_name='Entries', db_column='RateTable_id',  # 保持与主服务器数据库实际列名一致
    )
    RateKey = models.CharField(max_length=64)
    RateLabel = models.CharField(max_length=128, default='')
    RateValue = models.DecimalField(max_digits=10, decimal_places=6)
    EntryStatus = models.SmallIntegerField(default=1)
    CreateTime = models.DateTimeField(default=_timezone.now)

    class Meta:
        db_table = 'CommissionRateEntry'
        app_label = 'gvsdsdk'
        verbose_name = '费率条目'
        verbose_name_plural = '费率条目'
        unique_together = [('Table', 'RateKey')]
        managed = False
        # 索引由业务侧（gvsds）迁移声明；SDK 为 managed=False 镜像，不重复声明（避免 models.E030）

    def __str__(self):
        return f'{self.RateKey}={self.RateValue}'


class CommissionRule(QModel):
    """分佣规则表——定义"怎么分"

    每条规则绑定一种事件类型，定义参与者如何分账。
    支持比例分账、固定金额、阶梯分账、剩余归平台四种 SplitType。
    """

    SPLT_PERCENTAGE = 'percentage'
    SPLT_FIXED = 'fixed'
    SPLT_TIERED = 'tiered'
    SPLT_REMAINDER = 'remainder'

    SPLT_TYPE_CHOICES = [
        (SPLT_PERCENTAGE, '比例分账'),
        (SPLT_FIXED, '固定金额'),
        (SPLT_TIERED, '阶梯分账'),
        (SPLT_REMAINDER, '剩余归平台'),
    ]

    RuleUUID = UUIDField(primary_key=True)
    TenantUUID = UUIDField()
    EventType = models.CharField(max_length=32, choices=CommissionEvent.EVT_TYPE_CHOICES)
    RuleName = models.CharField(max_length=128)
    RuleDescription = models.CharField(max_length=512, default='')
    ParticipantRules = models.JSONField(default=list)
    Table = models.ForeignKey(
        RateTable, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='Rules', db_column='RateTable_id',  # 保持与主服务器数据库实际列名一致
    )
    MaxInviteLevel = models.SmallIntegerField(default=2)
    RulePriority = models.IntegerField(default=0)
    RuleStatus = models.SmallIntegerField(default=1)
    CreateTime = models.DateTimeField(default=_timezone.now)
    UpdateTime = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'CommissionRule'
        app_label = 'gvsdsdk'
        verbose_name = '分佣规则'
        verbose_name_plural = '分佣规则'
        managed = False
        # 索引由业务侧（gvsds）迁移声明；SDK 为 managed=False 镜像，不重复声明（避免 models.E030）

    def __str__(self):
        return f'{self.RuleName}({self.EventType})'

    def FindParticipantRule(self, role):
        for pr in self.ParticipantRules:
            # 键名 PascalCase（与 manager.py 创建规则时一致）
            if pr.get('Role') == role or pr.get('role') == role:
                return pr
        return None


class CommissionRecord(QModel):
    """分佣记录表——不可变分账流水

    每条记录代表一次分账结果：谁、从哪个事件、按什么规则、分了多少。
    生成后即冻结，规则变更不影响历史记录。
    """

    REC_PENDING = 0
    REC_SETTLED = 1
    REC_WITHDRAWN = 2
    REC_CANCELLED = 3

    REC_STATUS_CHOICES = [
        (REC_PENDING, '待结算'),
        (REC_SETTLED, '已结算'),
        (REC_WITHDRAWN, '已提现'),
        (REC_CANCELLED, '已取消'),
    ]

    RecordUUID = UUIDField(primary_key=True)
    TenantUUID = UUIDField()
    Event = models.ForeignKey(
        CommissionEvent, on_delete=models.CASCADE,
        related_name='Records',
    )
    Rule = models.ForeignKey(
        CommissionRule, on_delete=models.SET_NULL,
        null=True, related_name='records',
    )
    ParticipantID = models.CharField(max_length=128)
    ParticipantRole = models.CharField(max_length=64)
    SplitType = models.CharField(max_length=16)
    Amount = models.DecimalField(max_digits=18, decimal_places=4)
    RuleSnapshot = models.JSONField(default=dict)
    Status = models.SmallIntegerField(default=REC_PENDING, choices=REC_STATUS_CHOICES)
    TierIndex = models.IntegerField(default=0)
    SettleTime = models.DateTimeField(null=True, blank=True)
    CreateTime = models.DateTimeField(default=_timezone.now)

    class Meta:
        db_table = 'CommissionRecord'
        app_label = 'gvsdsdk'
        verbose_name = '分佣记录'
        verbose_name_plural = '分佣记录'
        ordering = ['-CreateTime']
        managed = False
        # 索引由业务侧（gvsds）迁移声明；SDK 为 managed=False 镜像，不重复声明（避免 models.E030）

    def __str__(self):
        return f'{self.ParticipantRole}:{self.ParticipantID}={self.Amount}'

    @property
    def IsSettled(self):
        # 仅已结算/已提现视为已结算，排除已取消（REC_CANCELLED=3 > REC_SETTLED=1 会误判）
        return self.Status in (self.REC_SETTLED, self.REC_WITHDRAWN)

    def Settle(self):
        self.Status = self.REC_SETTLED
        self.SettleTime = _timezone.now()
        self.save(update_fields=['Status', 'SettleTime'])

    def Cancel(self):
        self.Status = self.REC_CANCELLED
        self.save(update_fields=['Status'])


class PreSettlement(QModel):
    """预结算表——跨平台订单待确认款项

    当订单涉及外部平台时，打手分成先挂预结算，
    对方确认后转为正式 CommissionRecord。
    """

    PST_PENDING = 0
    PST_CONFIRMED = 1
    PST_REJECTED = 2

    PST_STATUS_CHOICES = [
        (PST_PENDING, '待确认'),
        (PST_CONFIRMED, '已确认'),
        (PST_REJECTED, '已拒绝'),
    ]

    SettlementUUID = UUIDField(primary_key=True)
    TenantUUID = UUIDField()
    EventKey = models.CharField(max_length=128)
    ExternalPlatform = models.CharField(max_length=64, default='')
    ExternalOrderID = models.CharField(max_length=128, default='')
    ParticipantID = models.CharField(max_length=128)
    ParticipantRole = models.CharField(max_length=64, default='worker')
    Amount = models.DecimalField(max_digits=18, decimal_places=4)
    OrderAmount = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    Status = models.SmallIntegerField(default=PST_PENDING, choices=PST_STATUS_CHOICES)
    ConfirmedBy = models.CharField(max_length=128, default='')
    ConfirmDeadline = models.DateTimeField(null=True, blank=True)
    ConfirmedAt = models.DateTimeField(null=True, blank=True)
    CreateTime = models.DateTimeField(default=_timezone.now)
    UpdateTime = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'CommissionPreSettlement'
        app_label = 'gvsdsdk'
        verbose_name = '预结算'
        verbose_name_plural = '预结算'
        unique_together = [('EventKey', 'ParticipantID')]
        managed = False
        # 索引由业务侧（gvsds）迁移声明；SDK 为 managed=False 镜像，不重复声明（避免 models.E030）

    def __str__(self):
        return f'预结算:{self.EventKey}->{self.ParticipantID}={self.Amount}'

    def Confirm(self, confirmed_by=''):
        self.Status = self.PST_CONFIRMED
        self.ConfirmedBy = confirmed_by
        self.ConfirmedAt = _timezone.now()
        self.UpdateTime = _timezone.now()
        self.save(update_fields=['Status', 'ConfirmedBy', 'ConfirmedAt', 'UpdateTime'])

    def Reject(self, confirmed_by=''):
        self.Status = self.PST_REJECTED
        self.ConfirmedBy = confirmed_by
        self.ConfirmedAt = _timezone.now()
        self.UpdateTime = _timezone.now()
        self.save(update_fields=['Status', 'ConfirmedBy', 'ConfirmedAt', 'UpdateTime'])

"""模块：MSync 数据模型
微服务通信核心表：服务节点、服务令牌、对象全局映射（OGM）、分布式事务。
所有 ORM 字段与方法遵循帕斯卡命名法。

模型归属 app_label='msync'，与 gvsds 主服务器 7 库路由及既有迁移兼容。"""

import uuid
import datetime
from django.db import models
from gvsdsdk.model_base import QModel
from gvsdsdk.models import UUIDField


def _utcnow():
    """返回 naive UTC datetime（兼容 Django USE_TZ=False）"""
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


class ServiceNode(QModel):
    """服务节点表——注册的微服务实例（账本服务器存储的所有分布式服务地址）"""

    ServiceUUID = UUIDField(primary_key=True, verbose_name='服务UUID')
    ServiceName = models.CharField(max_length=128, unique=True, verbose_name='服务名称')
    ServiceDomain = models.CharField(max_length=256, verbose_name='服务域名')
    ServiceSecretHash = models.CharField(max_length=128, verbose_name='密钥哈希')
    ServiceSecretEncrypted = models.CharField(max_length=512, default='', verbose_name='加密密钥')
    ServiceScope = models.JSONField(default=list, verbose_name='服务权限')
    ServiceStatus = models.SmallIntegerField(default=1, verbose_name='服务状态')
    ServiceType = models.SmallIntegerField(default=0, verbose_name='服务类型')
    ServiceMetadata = models.JSONField(default=dict, verbose_name='服务元数据')
    CreateTime = models.DateTimeField(default=_utcnow, verbose_name='创建时间')
    LastHeartbeat = models.DateTimeField(null=True, blank=True, verbose_name='最后心跳')

    class Meta:
        db_table = 'ServiceNode'
        app_label = 'msync'
        verbose_name = '服务节点'
        verbose_name_plural = '服务节点'
        ordering = ['-CreateTime']

    def __str__(self):
        return self.ServiceName

    @property
    def IsMaster(self):
        return self.ServiceType == 1

    @property
    def IsActive(self):
        return self.ServiceStatus == 1

    def VerifySecret(self, raw_secret):
        import bcrypt
        try:
            return bcrypt.checkpw(
                raw_secret.encode('utf-8'),
                self.ServiceSecretHash.encode('utf-8')
            )
        except Exception:
            return False

    def SetSecret(self, raw_secret):
        from gvsdsdk.auth.crypto import SymmetricEncrypt
        from gvsdsdk.msync.auth import _get_master_secret
        master = _get_master_secret()
        self.ServiceSecretEncrypted = SymmetricEncrypt(raw_secret, master)

    def GetSecret(self):
        if not self.ServiceSecretEncrypted:
            return None
        from gvsdsdk.auth.crypto import SymmetricDecrypt
        from gvsdsdk.msync.auth import _get_master_secret
        master = _get_master_secret()
        try:
            return SymmetricDecrypt(self.ServiceSecretEncrypted, master)
        except Exception:
            return None

    def HasScope(self, scope_name):
        if not self.ServiceScope:
            return False
        return scope_name in self.ServiceScope or '*' in self.ServiceScope


class ServiceToken(QModel):
    """服务令牌表——服务间认证凭据"""

    TokenUUID = UUIDField(primary_key=True, verbose_name='令牌UUID')
    ServiceUUID = models.ForeignKey(
        ServiceNode, on_delete=models.CASCADE,
        db_column='ServiceUUID',
        related_name='tokens', verbose_name='服务UUID',
    )
    AccessToken = models.CharField(max_length=512, unique=True, verbose_name='访问令牌')
    RefreshToken = models.CharField(max_length=512, unique=True, verbose_name='刷新令牌')
    TokenScope = models.JSONField(default=list, verbose_name='令牌权限')
    ExpiresAt = models.DateTimeField(verbose_name='过期时间')
    RefreshExpiresAt = models.DateTimeField(verbose_name='刷新过期时间')
    CreateTime = models.DateTimeField(default=_utcnow, verbose_name='创建时间')
    TokenStatus = models.SmallIntegerField(default=1, verbose_name='令牌状态')

    class Meta:
        db_table = 'ServiceToken'
        app_label = 'msync'
        verbose_name = '服务令牌'
        verbose_name_plural = '服务令牌'
        ordering = ['-CreateTime']

    def IsExpired(self):
        return _utcnow() > self.ExpiresAt.replace(tzinfo=None)

    def IsRefreshExpired(self):
        return _utcnow() > self.RefreshExpiresAt.replace(tzinfo=None)

    def IsValid(self):
        return self.TokenStatus == 1 and not self.IsExpired()

    def Revoke(self):
        self.TokenStatus = 0
        self.save(update_fields=['TokenStatus'])


class ObjectGlobalMapping(QModel):
    """对象全局映射表——跨服务对象引用注册"""

    OGMUUID = UUIDField(primary_key=True, verbose_name='OGM UUID')
    ObjectType = models.CharField(max_length=64, verbose_name='对象类型')
    ObjectLocalID = models.CharField(max_length=128, verbose_name='本地ID')
    ObjectGlobalID = models.CharField(max_length=128, unique=True, db_index=True, verbose_name='全局ID')
    OwnerServiceUUID = models.ForeignKey(
        ServiceNode, on_delete=models.CASCADE,
        db_column='OwnerServiceUUID',
        related_name='owned_objects', verbose_name='所属服务UUID',
    )
    ObjectMetadata = models.JSONField(default=dict, verbose_name='对象元数据')
    ObjectStatus = models.SmallIntegerField(default=1, verbose_name='对象状态')
    CreateTime = models.DateTimeField(default=_utcnow, verbose_name='创建时间')
    UpdateTime = models.DateTimeField(null=True, blank=True, verbose_name='更新时间')

    class Meta:
        db_table = 'OGM'
        app_label = 'msync'
        verbose_name = '对象全局映射'
        verbose_name_plural = '对象全局映射'
        unique_together = [('ObjectType', 'ObjectLocalID', 'OwnerServiceUUID')]
        ordering = ['-CreateTime']
        indexes = [
            models.Index(fields=['ObjectType'], name='idx_ogm_type'),
            models.Index(fields=['ObjectGlobalID'], name='idx_ogm_gid'),
        ]

    def __str__(self):
        return f'{self.ObjectType}:{self.ObjectGlobalID}'

    @property
    def IsAlive(self):
        return self.ObjectStatus == 1

    def Touch(self):
        self.UpdateTime = _utcnow()
        self.save(update_fields=['UpdateTime'])


class DistributedTransaction(QModel):
    """分布式事务表——跨服务事务编排（Saga 模式）"""

    TX_STATUS_PENDING = 0
    TX_STATUS_RUNNING = 1
    TX_STATUS_COMMITTED = 2
    TX_STATUS_COMPENSATING = 3
    TX_STATUS_COMPENSATED = 4
    TX_STATUS_FAILED = 5
    TX_STATUS_TIMED_OUT = 6

    TX_STATUS_CHOICES = [
        (TX_STATUS_PENDING, '待执行'),
        (TX_STATUS_RUNNING, '执行中'),
        (TX_STATUS_COMMITTED, '已提交'),
        (TX_STATUS_COMPENSATING, '补偿中'),
        (TX_STATUS_COMPENSATED, '已补偿'),
        (TX_STATUS_FAILED, '失败'),
        (TX_STATUS_TIMED_OUT, '超时'),
    ]

    TX_TYPE_SAGA = 'saga'
    TX_TYPE_TWO_PHASE = '2pc'

    TX_TYPE_CHOICES = [
        (TX_TYPE_SAGA, 'Saga'),
        (TX_TYPE_TWO_PHASE, '两阶段提交'),
    ]

    TransactionUUID = UUIDField(primary_key=True, verbose_name='事务UUID')
    TransactionCode = models.CharField(max_length=64, unique=True, verbose_name='事务编号')
    TransactionType = models.CharField(max_length=16, choices=TX_TYPE_CHOICES, default=TX_TYPE_SAGA, verbose_name='事务类型')
    TransactionName = models.CharField(max_length=128, verbose_name='事务名称')
    TransactionDescription = models.CharField(max_length=512, default='', verbose_name='事务描述')
    TransactionStatus = models.SmallIntegerField(
        default=TX_STATUS_PENDING, choices=TX_STATUS_CHOICES, verbose_name='事务状态',
    )
    InitiatorServiceUUID = models.ForeignKey(
        ServiceNode, on_delete=models.SET_NULL,
        null=True, blank=True, db_column='InitiatorServiceUUID',
        related_name='initiated_dtx', verbose_name='发起服务UUID',
    )
    ReferenceOGMUUID = UUIDField(null=True, blank=True, verbose_name='关联OGM UUID')
    TransactionPayload = models.JSONField(default=dict, verbose_name='事务载荷')
    TransactionResult = models.JSONField(default=dict, verbose_name='事务结果')
    TimeoutSeconds = models.IntegerField(default=300, verbose_name='超时秒数')
    CreateTime = models.DateTimeField(default=_utcnow, verbose_name='创建时间')
    UpdateTime = models.DateTimeField(null=True, blank=True, verbose_name='更新时间')
    EndTime = models.DateTimeField(null=True, blank=True, verbose_name='结束时间')

    class Meta:
        db_table = 'DistributedTransaction'
        app_label = 'msync'
        verbose_name = '分布式事务'
        verbose_name_plural = '分布式事务'
        ordering = ['-CreateTime']
        indexes = [
            models.Index(fields=['TransactionStatus'], name='idx_dtx_status'),
            models.Index(fields=['InitiatorServiceUUID'], name='idx_dtx_initiator'),
        ]

    def __str__(self):
        return self.TransactionCode

    @property
    def IsTerminal(self):
        return self.TransactionStatus in (
            self.TX_STATUS_COMMITTED,
            self.TX_STATUS_COMPENSATED,
            self.TX_STATUS_FAILED,
            self.TX_STATUS_TIMED_OUT,
        )

    @property
    def IsTimedOut(self):
        # 空闲超时：以最后活动时间（UpdateTime）起算，未活动过则退回创建时间
        if self.TransactionStatus in (self.TX_STATUS_COMMITTED, self.TX_STATUS_COMPENSATED):
            return False
        if not self.TimeoutSeconds:
            return False
        baseline = self.UpdateTime or self.CreateTime
        if not baseline:
            return False
        elapsed = (_utcnow() - baseline.replace(tzinfo=None)).total_seconds()
        return elapsed > self.TimeoutSeconds


class TransactionStep(QModel):
    """事务步骤表——Saga 编排中的每一步"""

    STEP_STATUS_PENDING = 0
    STEP_STATUS_RUNNING = 1
    STEP_STATUS_COMPLETED = 2
    STEP_STATUS_COMPENSATING = 3
    STEP_STATUS_COMPENSATED = 4
    STEP_STATUS_FAILED = 5
    STEP_STATUS_SKIPPED = 6

    STEP_STATUS_CHOICES = [
        (STEP_STATUS_PENDING, '待执行'),
        (STEP_STATUS_RUNNING, '执行中'),
        (STEP_STATUS_COMPLETED, '已完成'),
        (STEP_STATUS_COMPENSATING, '补偿中'),
        (STEP_STATUS_COMPENSATED, '已补偿'),
        (STEP_STATUS_FAILED, '失败'),
        (STEP_STATUS_SKIPPED, '已跳过'),
    ]

    StepUUID = UUIDField(primary_key=True, verbose_name='步骤UUID')
    TransactionUUID = models.ForeignKey(
        DistributedTransaction, on_delete=models.CASCADE,
        db_column='TransactionUUID',
        related_name='steps', verbose_name='事务UUID',
    )
    StepIndex = models.IntegerField(verbose_name='步骤序号')
    StepName = models.CharField(max_length=128, verbose_name='步骤名称')
    StepDescription = models.CharField(max_length=512, default='', verbose_name='步骤描述')
    ParticipantServiceUUID = models.ForeignKey(
        ServiceNode, on_delete=models.SET_NULL,
        null=True, blank=True, db_column='ParticipantServiceUUID',
        related_name='participated_steps', verbose_name='参与服务UUID',
    )
    StepStatus = models.SmallIntegerField(
        default=STEP_STATUS_PENDING, choices=STEP_STATUS_CHOICES, verbose_name='步骤状态',
    )
    ActionEndpoint = models.CharField(max_length=512, verbose_name='执行端点')
    ActionMethod = models.CharField(max_length=8, default='POST', verbose_name='执行方法')
    ActionPayload = models.JSONField(default=dict, verbose_name='执行载荷')
    CompensateEndpoint = models.CharField(max_length=512, default='', verbose_name='补偿端点')
    CompensateMethod = models.CharField(max_length=8, default='POST', verbose_name='补偿方法')
    CompensatePayload = models.JSONField(default=dict, verbose_name='补偿载荷')
    StepResult = models.JSONField(default=dict, verbose_name='步骤结果')
    RetryCount = models.IntegerField(default=0, verbose_name='重试次数')
    MaxRetries = models.IntegerField(default=3, verbose_name='最大重试')
    CreateTime = models.DateTimeField(default=_utcnow, verbose_name='创建时间')
    StartTime = models.DateTimeField(null=True, blank=True, verbose_name='开始时间')
    EndTime = models.DateTimeField(null=True, blank=True, verbose_name='结束时间')

    class Meta:
        db_table = 'TransactionStep'
        app_label = 'msync'
        verbose_name = '事务步骤'
        verbose_name_plural = '事务步骤'
        ordering = ['TransactionUUID', 'StepIndex']
        indexes = [
            models.Index(fields=['TransactionUUID', 'StepIndex'], name='idx_step_tx_order'),
        ]

    def __str__(self):
        return f'{self.StepIndex}: {self.StepName}'

    @property
    def CanRetry(self):
        return self.RetryCount < self.MaxRetries

    @property
    def IsTerminal(self):
        return self.StepStatus in (
            self.STEP_STATUS_COMPLETED,
            self.STEP_STATUS_COMPENSATED,
            self.STEP_STATUS_FAILED,
            self.STEP_STATUS_SKIPPED,
        )

"""模块：每日数据统计引擎 — SAAS 数据模型（gvsdsdk 副本）
抽象统计层：不绑定具体业务，由租户定义统计指标和维度。
与 gvsds/apps/stats/models.py 字段、db_table 完全一致，映射 SAAS 主库同名表。

核心设计：
  StatMetric    — 统计指标定义（计数/金额/自定义）
  StatDimension — 统计维度定义（按角色/按类型/按来源）
  StatRecord    — 统计流水表（按维度+时间窗口聚合的统计数据）
  StatRanking   — 排行榜快照（日榜/月榜）
  StatConfig    — 统计配置（周期重置、排行榜规则）"""

import uuid
import datetime
from django.db import models
from gvsdsdk.model_base import QModel
from gvsdsdk.models import UUIDField


class StatMetric(QModel):
    """统计指标定义——租户自定义要统计什么

    每个租户可定义多个指标，例如：
      · dashou_jiedan_count   — 打手接单数
      · dashou_shouru_amount  — 打手收入金额
      · guanshi_chongzhi_count — 管事充值人数
      · platform_total_income — 平台总收入
    """

    MET_COUNT = 'count'
    MET_AMOUNT = 'amount'
    MET_DURATION = 'duration'
    MET_RATIO = 'ratio'
    MET_CUSTOM = 'custom'

    MET_TYPE_CHOICES = [
        (MET_COUNT, '计数'),
        (MET_AMOUNT, '金额/数值'),
        (MET_DURATION, '时长'),
        (MET_RATIO, '比率'),
        (MET_CUSTOM, '自定义'),
    ]

    MetricUUID = UUIDField(primary_key=True, verbose_name='指标UUID')
    TenantUUID = UUIDField(verbose_name='租户UUID')
    MetricCode = models.CharField(max_length=64, verbose_name='指标编码')
    MetricName = models.CharField(max_length=128, verbose_name='指标名称')
    MetricType = models.CharField(max_length=16, choices=MET_TYPE_CHOICES, default=MET_COUNT, verbose_name='指标类型')
    Unit = models.CharField(max_length=16, default='次', verbose_name='单位')
    DefaultValue = models.DecimalField(max_digits=18, decimal_places=4, default=0, verbose_name='默认值')
    MetricStatus = models.SmallIntegerField(default=1, verbose_name='指标状态')
    CreateTime = models.DateTimeField(default=datetime.datetime.utcnow, verbose_name='创建时间')

    class Meta:
        db_table = 'StatMetric'
        app_label = 'gvsdsdk'
        verbose_name = '统计指标'
        verbose_name_plural = '统计指标'
        unique_together = [('TenantUUID', 'MetricCode')]
        managed = False
        # 索引由业务侧（gvsds）迁移声明；SDK 为 managed=False 镜像，不重复声明（避免 models.E030）

    def __str__(self):
        return f'{self.MetricCode}:{self.MetricName}'


class StatDimension(QModel):
    """统计维度定义——按什么切分统计

    每个指标可以有多个维度，例如：
      · role       — 按角色（打手/管事/组长/商家）
      · platform   — 按平台（微信/支付宝/银行卡）
      · order_type — 按订单类型（平台订单/商家订单）
      · source     — 按来源渠道
    """

    DimensionUUID = UUIDField(primary_key=True, verbose_name='维度UUID')
    TenantUUID = UUIDField(verbose_name='租户UUID')
    DimensionCode = models.CharField(max_length=64, verbose_name='维度编码')
    DimensionName = models.CharField(max_length=128, verbose_name='维度名称')
    DimensionStatus = models.SmallIntegerField(default=1, verbose_name='维度状态')
    CreateTime = models.DateTimeField(default=datetime.datetime.utcnow, verbose_name='创建时间')

    class Meta:
        db_table = 'StatDimension'
        app_label = 'gvsdsdk'
        verbose_name = '统计维度'
        verbose_name_plural = '统计维度'
        unique_together = [('TenantUUID', 'DimensionCode')]
        managed = False

    def __str__(self):
        return f'{self.DimensionCode}:{self.DimensionName}'


class StatRecord(QModel):
    """统计记录表——按维度+时间窗口聚合的统计数据

    核心结构: 租户 + 日期 + 指标 + 维度值 → 数值

    用法:
      租户 'al_long'，日期 '2026-06-11'，
      指标 'dashou_jiedan_count'，维度值 {'role': 'dashou', 'user_id': '123'}
      → value = 5（今天打手123接了5单）
    """

    PERIOD_DAILY = 'daily'
    PERIOD_HOURLY = 'hourly'
    PERIOD_MONTHLY = 'monthly'
    PERIOD_CUSTOM = 'custom'

    PERIOD_CHOICES = [
        (PERIOD_DAILY, '每日'),
        (PERIOD_HOURLY, '每小时'),
        (PERIOD_MONTHLY, '每月'),
        (PERIOD_CUSTOM, '自定义'),
    ]

    RecordUUID = UUIDField(primary_key=True, verbose_name='记录UUID')
    TenantUUID = UUIDField(verbose_name='租户UUID')
    PeriodType = models.CharField(max_length=16, choices=PERIOD_CHOICES, default=PERIOD_DAILY, verbose_name='周期类型')
    PeriodKey = models.CharField(max_length=32, verbose_name='周期键')
    Metric = models.ForeignKey(
        StatMetric, on_delete=models.CASCADE,
        related_name='records',
        verbose_name='统计指标',
    )
    DimensionValues = models.JSONField(default=dict, verbose_name='维度值')
    Value = models.DecimalField(max_digits=18, decimal_places=4, default=0, verbose_name='数值')
    UpdateTime = models.DateTimeField(default=datetime.datetime.utcnow, verbose_name='更新时间')
    CreateTime = models.DateTimeField(default=datetime.datetime.utcnow, verbose_name='创建时间')

    class Meta:
        db_table = 'StatRecord'
        app_label = 'gvsdsdk'
        verbose_name = '统计记录'
        verbose_name_plural = '统计记录'
        unique_together = [('TenantUUID', 'PeriodType', 'PeriodKey', 'Metric', 'DimensionValues')]
        managed = False
        # 索引由业务侧（gvsds）迁移声明；SDK 为 managed=False 镜像，不重复声明（避免 models.E030）

    def __str__(self):
        dim_str = ','.join(f'{k}={v}' for k, v in self.DimensionValues.items())
        return f'{self.PeriodKey}/{self.Metric.MetricCode}/{dim_str}={self.Value}'


class StatRanking(QModel):
    """排行榜快照——周期性的排名记录

    按指标+维度分组，在指定周期内的排名快照。
    通常由定时任务生成，前端直接查询即可。
    """

    RNK_DAILY = 'daily'
    RNK_MONTHLY = 'monthly'

    RNK_PERIOD_CHOICES = [
        (RNK_DAILY, '日榜'),
        (RNK_MONTHLY, '月榜'),
    ]

    RankingUUID = UUIDField(primary_key=True, verbose_name='排行UUID')
    TenantUUID = UUIDField(verbose_name='租户UUID')
    RankingPeriod = models.CharField(max_length=16, choices=RNK_PERIOD_CHOICES, verbose_name='排行周期')
    RankingDate = models.DateField(verbose_name='排行日期')
    Metric = models.ForeignKey(
        StatMetric, on_delete=models.CASCADE,
        related_name='rankings',
        verbose_name='统计指标',
    )
    DimensionFilter = models.JSONField(default=dict, verbose_name='维度筛选')
    TargetID = models.CharField(max_length=128, verbose_name='目标ID')
    Rank = models.IntegerField(verbose_name='排名')
    RankValue = models.DecimalField(max_digits=18, decimal_places=4, verbose_name='排名数值')
    TargetName = models.CharField(max_length=128, default='', verbose_name='目标名称')
    TargetAvatar = models.CharField(max_length=512, default='', verbose_name='目标头像')
    CreateTime = models.DateTimeField(default=datetime.datetime.utcnow, verbose_name='创建时间')

    class Meta:
        db_table = 'StatRanking'
        app_label = 'gvsdsdk'
        verbose_name = '排行榜'
        verbose_name_plural = '排行榜'
        unique_together = [('TenantUUID', 'RankingPeriod', 'RankingDate', 'Metric', 'DimensionFilter', 'Rank')]
        managed = False
        # 索引由业务侧（gvsds）迁移声明；SDK 为 managed=False 镜像，不重复声明（避免 models.E030）

    def __str__(self):
        return f'{self.RankingDate} {self.RankingPeriod} #{self.Rank} {self.TargetID}'


class StatConfig(QModel):
    """统计配置——租户级统计行为配置

    定义：
      · 哪些指标需要周期性重置（日清零/月清零）
      · 排行榜生成规则
      · 数据保留天数
    """

    ConfigUUID = UUIDField(primary_key=True, verbose_name='配置UUID')
    TenantUUID = UUIDField(verbose_name='租户UUID')
    Metric = models.ForeignKey(
        StatMetric, on_delete=models.CASCADE,
        related_name='configs',
        verbose_name='统计指标',
    )
    AutoReset = models.BooleanField(default=False, verbose_name='自动重置')
    ResetCron = models.CharField(max_length=64, default='0 0 * * *', verbose_name='重置Cron表达式')
    EnableRanking = models.BooleanField(default=False, verbose_name='启用排行榜')
    RankingDimensionFilter = models.JSONField(default=dict, verbose_name='排行维度筛选')
    RankingTopN = models.IntegerField(default=50, verbose_name='排行TopN')
    RankingSortOrder = models.CharField(max_length=8, default='desc', verbose_name='排行排序方式')
    RetentionDays = models.IntegerField(default=365, verbose_name='数据保留天数')
    ConfigStatus = models.SmallIntegerField(default=1, verbose_name='配置状态')
    CreateTime = models.DateTimeField(default=datetime.datetime.utcnow, verbose_name='创建时间')

    class Meta:
        db_table = 'StatConfig'
        app_label = 'gvsdsdk'
        verbose_name = '统计配置'
        verbose_name_plural = '统计配置'
        unique_together = [('TenantUUID', 'Metric')]
        managed = False

    def __str__(self):
        return f'StatConfig:{self.Metric.MetricCode}'

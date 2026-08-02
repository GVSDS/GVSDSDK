"""模块：订单 SAAS 数据模型
SAAS 级订单模板与订单实例——支持等级定价、可选绑定支付网关。

设计原则：
    1. 模板驱动：通过 OrderTemplate 定义订单结构，子服务基于模板快速创建订单
    2. 等级定价：OrderLevel 定义不同等级的价格/权益，订单可关联等级
    3. 支付可选：订单可选绑定 PaymentGatewayConfig，不绑定则不涉及支付
    4. 不可变快照：订单创建后，模板变更不影响已有订单
"""

from django.db import models
from django.utils import timezone as _timezone
from gvsdsdk.model_base import QModel
from gvsdsdk.models import UUIDField


# === 订单等级表 ===

class OrderLevel(QModel):
    """订单等级表——定义订单模板的定价阶梯

    例如：VIP 月卡、VIP 季卡、SVIP 年卡等。
    每个等级属于一个订单模板，有独立的价格和有效期。
    """

    LevelUUID = UUIDField(primary_key=True, verbose_name='等级UUID')
    Template = models.ForeignKey(
        'OrderTemplate', on_delete=models.CASCADE,
        related_name='Levels',
        verbose_name='订单模板',
    )
    LevelCode = models.CharField(max_length=64, verbose_name='等级编码')
    LevelName = models.CharField(max_length=128, verbose_name='等级名称')
    LevelDescription = models.CharField(max_length=512, default='', verbose_name='等级描述')
    Price = models.DecimalField(max_digits=18, decimal_places=4, default=0, verbose_name='价格')
    OriginalPrice = models.DecimalField(max_digits=18, decimal_places=4, default=0, verbose_name='原价')
    Currency = models.CharField(max_length=10, default='CNY', verbose_name='币种')
    DurationDays = models.IntegerField(default=0, verbose_name='有效天数')
    DurationMonths = models.IntegerField(default=0, verbose_name='有效月数')
    LevelSort = models.SmallIntegerField(default=0, verbose_name='排序')
    LevelStatus = models.SmallIntegerField(default=1, verbose_name='等级状态')
    LevelMetadata = models.JSONField(default=dict, verbose_name='等级元数据')
    CreateTime = models.DateTimeField(default=_timezone.now, verbose_name='创建时间')
    UpdateTime = models.DateTimeField(null=True, blank=True, verbose_name='更新时间')

    class Meta:
        db_table = 'OrderLevel'
        app_label = 'gvsdsdk'
        verbose_name = '订单等级'
        verbose_name_plural = '订单等级'
        managed = False
        unique_together = [('Template', 'LevelCode')]
        ordering = ['LevelSort']
        indexes = [
            models.Index(fields=['Template', 'LevelStatus'], name='idx_ordlvl_template_status'),
        ]

    def __str__(self):
        return f'{self.LevelName}({self.LevelCode})'

    @property
    def IsActive(self):
        return self.LevelStatus == 1

    def GetDuration(self):
        """获取有效时长（天）"""
        return self.DurationDays + self.DurationMonths * 30


# === 订单模板表 ===

class OrderTemplate(QModel):
    """订单模板表——定义订单的结构与配置

    每个模板属于一个租户（TenantUUID），可选关联一个公司（CompanyUUID）。
    模板定义了订单的基本信息、支付配置、等级设置等。
    子服务通过模板快速创建标准化的订单。
    """

    TPL_SERVICE = 'service'
    TPL_GOODS = 'goods'
    TPL_MEMBERSHIP = 'membership'
    TPL_RECHARGE = 'recharge'
    TPL_CUSTOM = 'custom'

    TPL_TYPE_CHOICES = [
        (TPL_SERVICE, '服务'),
        (TPL_GOODS, '商品'),
        (TPL_MEMBERSHIP, '会员'),
        (TPL_RECHARGE, '充值'),
        (TPL_CUSTOM, '自定义'),
    ]

    TPL_DRAFT = 0
    TPL_ACTIVE = 1
    TPL_ARCHIVED = 2

    TPL_STATUS_CHOICES = [
        (TPL_DRAFT, '草稿'),
        (TPL_ACTIVE, '启用'),
        (TPL_ARCHIVED, '归档'),
    ]

    TemplateUUID = UUIDField(primary_key=True, verbose_name='模板UUID')
    TenantUUID = UUIDField(verbose_name='租户UUID')
    CompanyUUID = UUIDField(null=True, blank=True, verbose_name='公司UUID')
    TemplateCode = models.CharField(max_length=64, verbose_name='模板编码')
    TemplateName = models.CharField(max_length=256, verbose_name='模板名称')
    TemplateDescription = models.CharField(max_length=512, default='', verbose_name='模板描述')
    TemplateType = models.CharField(max_length=32, choices=TPL_TYPE_CHOICES, verbose_name='模板类型')
    TemplateStatus = models.SmallIntegerField(
        default=TPL_DRAFT, choices=TPL_STATUS_CHOICES,
        verbose_name='模板状态',
    )
    # 可选绑定支付网关
    PaymentConfig = models.ForeignKey(
        'payment.PaymentGatewayConfig', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='order_templates',
        verbose_name='支付网关配置',
    )
    # 订单级配置
    RequirePayment = models.BooleanField(default=True, verbose_name='需要支付')
    AllowRefund = models.BooleanField(default=False, verbose_name='允许退款')
    RefundPolicy = models.JSONField(default=dict, verbose_name='退款策略')
    AutoConfirm = models.BooleanField(default=False, verbose_name='自动确认')
    ConfirmTimeoutSeconds = models.IntegerField(default=0, verbose_name='确认超时秒数')
    # 模板元数据（扩展字段）
    TemplateMetadata = models.JSONField(default=dict, verbose_name='模板元数据')
    TemplateSort = models.SmallIntegerField(default=0, verbose_name='排序')
    CreateTime = models.DateTimeField(default=_timezone.now, verbose_name='创建时间')
    UpdateTime = models.DateTimeField(null=True, blank=True, verbose_name='更新时间')

    class Meta:
        db_table = 'OrderTemplate'
        app_label = 'gvsdsdk'
        verbose_name = '订单模板'
        verbose_name_plural = '订单模板'
        managed = False
        unique_together = [('TenantUUID', 'TemplateCode')]
        ordering = ['TemplateSort', '-CreateTime']
        indexes = [
            models.Index(fields=['TenantUUID', 'TemplateStatus'], name='idx_ordtpl_tenant_status'),
            models.Index(fields=['TemplateType'], name='idx_ordtpl_type'),
        ]

    def __str__(self):
        return f'{self.TemplateName}({self.TemplateCode})'

    @property
    def IsActive(self):
        return self.TemplateStatus == self.TPL_ACTIVE

    def GetLevels(self):
        """获取模板的所有等级"""
        return self.Levels.filter(LevelStatus=1).order_by('LevelSort')

    def GetItems(self):
        """获取模板的所有行项目定义"""
        return self.Items.filter(ItemStatus=1).order_by('ItemSort')


# === 订单模板项表 ===

class OrderTemplateItem(QModel):
    """订单模板项表——定义模板中的行项目

    每个行项目代表订单中的一个条目，如商品、服务项等。
    创建订单时，模板项会被复制到订单项中。
    """

    ITEM_MANDATORY = 0
    ITEM_OPTIONAL = 1

    ITEM_OPTIONALITY_CHOICES = [
        (ITEM_MANDATORY, '必选'),
        (ITEM_OPTIONAL, '可选'),
    ]

    ItemUUID = UUIDField(primary_key=True, verbose_name='模板项UUID')
    Template = models.ForeignKey(
        OrderTemplate, on_delete=models.CASCADE,
        related_name='Items',
        verbose_name='订单模板',
    )
    ItemCode = models.CharField(max_length=64, verbose_name='项目编码')
    ItemName = models.CharField(max_length=256, verbose_name='项目名称')
    ItemDescription = models.CharField(max_length=512, default='', verbose_name='项目描述')
    ItemType = models.CharField(max_length=32, default='default', verbose_name='项目类型')
    UnitPrice = models.DecimalField(max_digits=18, decimal_places=4, default=0, verbose_name='单价')
    Currency = models.CharField(max_length=10, default='CNY', verbose_name='币种')
    DefaultQuantity = models.IntegerField(default=1, verbose_name='默认数量')
    MinQuantity = models.IntegerField(default=1, verbose_name='最小数量')
    MaxQuantity = models.IntegerField(default=999, verbose_name='最大数量')
    Optionality = models.SmallIntegerField(
        default=ITEM_MANDATORY, choices=ITEM_OPTIONALITY_CHOICES,
        verbose_name='是否可选',
    )
    ItemSort = models.SmallIntegerField(default=0, verbose_name='排序')
    ItemStatus = models.SmallIntegerField(default=1, verbose_name='项目状态')
    ItemMetadata = models.JSONField(default=dict, verbose_name='项目元数据')
    CreateTime = models.DateTimeField(default=_timezone.now, verbose_name='创建时间')

    class Meta:
        db_table = 'OrderTemplateItem'
        app_label = 'gvsdsdk'
        verbose_name = '订单模板项'
        verbose_name_plural = '订单模板项'
        managed = False
        unique_together = [('Template', 'ItemCode')]
        ordering = ['ItemSort']
        indexes = [
            models.Index(fields=['Template', 'ItemStatus'], name='idx_ordtplitem_template_status'),
        ]

    def __str__(self):
        return f'{self.ItemName}({self.ItemCode})'


# === 订单表 ===

class Order(QModel):
    """订单表——SAAS 级统一订单记录

    每个订单基于一个模板创建，包含订单基本信息、等级、支付状态等。
    子服务通过此表管理业务订单生命周期。
    """

    ORD_PENDING = 0
    ORD_CONFIRMED = 1
    ORD_PAID = 2
    ORD_PROCESSING = 3
    ORD_COMPLETED = 4
    ORD_CANCELLED = 5
    ORD_REFUNDING = 6
    ORD_REFUNDED = 7
    ORD_CLOSED = 8

    ORD_STATUS_CHOICES = [
        (ORD_PENDING, '待确认'),
        (ORD_CONFIRMED, '已确认'),
        (ORD_PAID, '已支付'),
        (ORD_PROCESSING, '处理中'),
        (ORD_COMPLETED, '已完成'),
        (ORD_CANCELLED, '已取消'),
        (ORD_REFUNDING, '退款中'),
        (ORD_REFUNDED, '已退款'),
        (ORD_CLOSED, '已关闭'),
    ]

    OrderUUID = UUIDField(primary_key=True, verbose_name='订单UUID')
    TenantUUID = UUIDField(verbose_name='租户UUID')
    CompanyUUID = UUIDField(null=True, blank=True, verbose_name='公司UUID')
    OrderNO = models.CharField(max_length=64, unique=True, verbose_name='订单编号')
    # 来源模板
    Template = models.ForeignKey(
        OrderTemplate, on_delete=models.SET_NULL,
        null=True, related_name='orders',
        verbose_name='订单模板',
    )
    # 等级
    Level = models.ForeignKey(
        OrderLevel, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='orders',
        verbose_name='订单等级',
    )
    # 订单主体
    OrderTitle = models.CharField(max_length=256, verbose_name='订单标题')
    OrderDescription = models.CharField(max_length=1024, default='', verbose_name='订单描述')
    OrderType = models.CharField(max_length=32, verbose_name='订单类型')
    # 用户
    BuyerUserUUID = UUIDField(verbose_name='买家用户UUID')
    SellerUserUUID = UUIDField(null=True, blank=True, verbose_name='卖家用户UUID')
    # 金额
    TotalAmount = models.DecimalField(max_digits=18, decimal_places=4, default=0, verbose_name='总金额')
    DiscountAmount = models.DecimalField(max_digits=18, decimal_places=4, default=0, verbose_name='优惠金额')
    PayAmount = models.DecimalField(max_digits=18, decimal_places=4, default=0, verbose_name='实付金额')
    Currency = models.CharField(max_length=10, default='CNY', verbose_name='币种')
    # 状态
    OrderStatus = models.SmallIntegerField(
        default=ORD_PENDING, choices=ORD_STATUS_CHOICES,
        verbose_name='订单状态',
    )
    # 支付关联（可选）
    PaymentTransaction = models.ForeignKey(
        'payment.PaymentTransaction', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='orders',
        verbose_name='支付交易',
    )
    PaymentConfig = models.ForeignKey(
        'payment.PaymentGatewayConfig', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='paid_orders',
        verbose_name='支付网关配置',
    )
    # 时间
    ConfirmTime = models.DateTimeField(null=True, blank=True, verbose_name='确认时间')
    PayTime = models.DateTimeField(null=True, blank=True, verbose_name='支付时间')
    CompleteTime = models.DateTimeField(null=True, blank=True, verbose_name='完成时间')
    CancelTime = models.DateTimeField(null=True, blank=True, verbose_name='取消时间')
    ExpireTime = models.DateTimeField(null=True, blank=True, verbose_name='过期时间')
    # 快照（创建时冻结模板信息）
    TemplateSnapshot = models.JSONField(default=dict, verbose_name='模板快照')
    LevelSnapshot = models.JSONField(default=dict, verbose_name='等级快照')
    # 扩展
    OrderMetadata = models.JSONField(default=dict, verbose_name='订单元数据')
    Remark = models.CharField(max_length=512, default='', verbose_name='备注')
    CreateTime = models.DateTimeField(default=_timezone.now, verbose_name='创建时间')
    UpdateTime = models.DateTimeField(null=True, blank=True, verbose_name='更新时间')

    class Meta:
        db_table = 'Order'
        app_label = 'gvsdsdk'
        verbose_name = '订单'
        verbose_name_plural = '订单'
        managed = False
        ordering = ['-CreateTime']
        indexes = [
            models.Index(fields=['TenantUUID', 'OrderStatus', 'CreateTime'], name='idx_order_tenant_status_time'),
            models.Index(fields=['BuyerUserUUID'], name='idx_order_buyer'),
            models.Index(fields=['OrderType'], name='idx_order_type'),
        ]

    def __str__(self):
        return self.OrderNO

    @property
    def IsPaid(self):
        # 已支付且未取消/退款/关闭（PAID=2, PROCESSING=3, COMPLETED=4）
        return self.ORD_PAID <= self.OrderStatus < self.ORD_CANCELLED

    @property
    def IsCompleted(self):
        return self.OrderStatus == self.ORD_COMPLETED

    @property
    def IsCancelled(self):
        return self.OrderStatus == self.ORD_CANCELLED

    @property
    def IsTerminal(self):
        return self.OrderStatus in (
            self.ORD_COMPLETED, self.ORD_CANCELLED,
            self.ORD_REFUNDED, self.ORD_CLOSED,
        )

    def Confirm(self):
        """确认订单（仅待确认状态可执行）"""
        if self.OrderStatus != self.ORD_PENDING:
            raise ValueError(f'订单当前状态({self.OrderStatus})不允许确认操作')
        self.OrderStatus = self.ORD_CONFIRMED
        self.ConfirmTime = _timezone.now()
        self.UpdateTime = _timezone.now()
        self.save(update_fields=['OrderStatus', 'ConfirmTime', 'UpdateTime'])

    def MarkPaid(self, payment_transaction=None):
        """标记为已支付（仅已确认状态可执行，与 OrderManager.FLOW_RULES 一致）"""
        if self.OrderStatus != self.ORD_CONFIRMED:
            raise ValueError(f'订单当前状态({self.OrderStatus})不允许支付操作，仅允许已确认状态')
        self.OrderStatus = self.ORD_PAID
        self.PayTime = _timezone.now()
        self.UpdateTime = _timezone.now()
        if payment_transaction:
            self.PaymentTransaction = payment_transaction
        self.save(update_fields=['OrderStatus', 'PayTime', 'PaymentTransaction', 'UpdateTime'])

    def StartProcessing(self):
        """开始处理（仅已支付状态可执行）"""
        if self.OrderStatus != self.ORD_PAID:
            raise ValueError(f'订单当前状态({self.OrderStatus})不允许开始处理')
        self.OrderStatus = self.ORD_PROCESSING
        self.UpdateTime = _timezone.now()
        self.save(update_fields=['OrderStatus', 'UpdateTime'])

    def Complete(self):
        """完成订单（处理中或已支付[简单订单]状态可执行）"""
        if self.OrderStatus not in (self.ORD_PAID, self.ORD_PROCESSING):
            raise ValueError(f'订单当前状态({self.OrderStatus})不允许完成操作')
        self.OrderStatus = self.ORD_COMPLETED
        self.CompleteTime = _timezone.now()
        self.UpdateTime = _timezone.now()
        self.save(update_fields=['OrderStatus', 'CompleteTime', 'UpdateTime'])

    def Cancel(self, reason=''):
        """取消订单（待确认或已确认状态可执行）"""
        if self.OrderStatus not in (self.ORD_PENDING, self.ORD_CONFIRMED):
            raise ValueError(f'订单当前状态({self.OrderStatus})不允许取消操作')
        self.OrderStatus = self.ORD_CANCELLED
        self.CancelTime = _timezone.now()
        self.UpdateTime = _timezone.now()
        if reason:
            self.Remark = reason
        self.save(update_fields=['OrderStatus', 'CancelTime', 'Remark', 'UpdateTime'])

    def StartRefund(self):
        """开始退款（已支付或处理中状态可执行）"""
        if self.OrderStatus not in (self.ORD_PAID, self.ORD_PROCESSING):
            raise ValueError(f'订单当前状态({self.OrderStatus})不允许退款操作')
        self.OrderStatus = self.ORD_REFUNDING
        self.UpdateTime = _timezone.now()
        self.save(update_fields=['OrderStatus', 'UpdateTime'])

    def RefundDone(self):
        """退款完成（仅退款中状态可执行）"""
        if self.OrderStatus != self.ORD_REFUNDING:
            raise ValueError(f'订单当前状态({self.OrderStatus})不允许完成退款')
        self.OrderStatus = self.ORD_REFUNDED
        self.UpdateTime = _timezone.now()
        self.save(update_fields=['OrderStatus', 'UpdateTime'])

    def Close(self):
        """关闭订单（终态前任意非关闭状态可执行）"""
        if self.OrderStatus == self.ORD_CLOSED:
            raise ValueError('订单已关闭，不可重复操作')
        self.OrderStatus = self.ORD_CLOSED
        self.UpdateTime = _timezone.now()
        self.save(update_fields=['OrderStatus', 'UpdateTime'])

    def GetItems(self):
        """获取订单所有行项目"""
        return self.Items.all().order_by('ItemSort')


# === 订单项表 ===

class OrderItem(QModel):
    """订单项表——订单中的行项目

    创建订单时从模板项复制生成，记录实际购买的商品/服务。
    """

    ItemUUID = UUIDField(primary_key=True, verbose_name='订单项UUID')
    Order = models.ForeignKey(
        Order, on_delete=models.CASCADE,
        related_name='Items',
        verbose_name='订单',
    )
    # 来源模板项（可选，手动创建的订单可能没有模板项）
    TemplateItem = models.ForeignKey(
        OrderTemplateItem, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='order_items',
        verbose_name='模板项',
    )
    ItemCode = models.CharField(max_length=64, verbose_name='项目编码')
    ItemName = models.CharField(max_length=256, verbose_name='项目名称')
    ItemDescription = models.CharField(max_length=512, default='', verbose_name='项目描述')
    ItemType = models.CharField(max_length=32, default='default', verbose_name='项目类型')
    UnitPrice = models.DecimalField(max_digits=18, decimal_places=4, default=0, verbose_name='单价')
    Quantity = models.IntegerField(default=1, verbose_name='数量')
    Subtotal = models.DecimalField(max_digits=18, decimal_places=4, default=0, verbose_name='小计')
    Currency = models.CharField(max_length=10, default='CNY', verbose_name='币种')
    ItemSort = models.SmallIntegerField(default=0, verbose_name='排序')
    ItemStatus = models.SmallIntegerField(default=1, verbose_name='项目状态')
    ItemMetadata = models.JSONField(default=dict, verbose_name='项目元数据')
    CreateTime = models.DateTimeField(default=_timezone.now, verbose_name='创建时间')

    class Meta:
        db_table = 'OrderItem'
        app_label = 'gvsdsdk'
        verbose_name = '订单项'
        verbose_name_plural = '订单项'
        managed = False
        ordering = ['ItemSort']
        indexes = [
            models.Index(fields=['Order'], name='idx_orditem_order'),
        ]

    def __str__(self):
        return f'{self.ItemName}x{self.Quantity}'

    def CalcSubtotal(self):
        """计算小计"""
        self.Subtotal = self.UnitPrice * self.Quantity
        return self.Subtotal

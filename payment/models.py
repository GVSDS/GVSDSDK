"""模块：支付 SAAS 数据模型
SAAS 级支付配置与交易流水——不绑定具体业务。
子服务自行关联 payment_transaction_id 到自己的订单。"""

import logging
from django.db import models, transaction
from django.utils import timezone as _timezone
from gvsdsdk.model_base import QModel
from gvsdsdk.models import UUIDField

logger = logging.getLogger(__name__)


class PaymentGatewayConfig(QModel):
    """支付网关配置表——每个租户每个渠道一条配置

    存储加密后的支付密钥，子服务通过此表配置自己的支付能力。
    SAAS 主服务器不关心具体业务，只提供通道。
    """

    ConfigUUID = UUIDField(primary_key=True)
    TenantUUID = UUIDField()
    Platform = models.CharField(max_length=32)
    AppID = models.CharField(max_length=64, default='')
    MchID = models.CharField(max_length=64, default='')
    APIKeyEncrypted = models.CharField(max_length=512, default='')
    PrivateKeyEncrypted = models.TextField(default='')
    PublicKeyEncrypted = models.TextField(default='')
    CertPath = models.CharField(max_length=512, default='')
    CertKeyPath = models.CharField(max_length=512, default='')
    NotifyURL = models.CharField(max_length=512, default='')
    ReturnURL = models.CharField(max_length=512, default='')
    ConfigExtra = models.JSONField(default=dict)
    ConfigStatus = models.SmallIntegerField(default=1)
    ConfigDefault = models.BooleanField(default=False)
    CreateTime = models.DateTimeField(default=_timezone.now)
    UpdateTime = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'PaymentGatewayConfig'
        app_label = 'gvsdsdk'
        verbose_name = '支付网关配置'
        verbose_name_plural = '支付网关配置'
        unique_together = [('TenantUUID', 'Platform')]
        managed = False
        indexes = [
            models.Index(fields=['TenantUUID'], name='idx_paycfg_tenant_sdk'),
            models.Index(fields=['Platform'], name='idx_paycfg_platform_sdk'),
        ]

    def __str__(self):
        return f'{self.Platform}@{self.TenantUUID}'

    @property
    def IsActive(self):
        return self.ConfigStatus == 1

    def DecryptAPIKey(self):
        if not self.APIKeyEncrypted:
            return ''
        return _decrypt_field(self.APIKeyEncrypted)

    def DecryptPrivateKey(self):
        if not self.PrivateKeyEncrypted:
            return ''
        return _decrypt_field(self.PrivateKeyEncrypted)

    def DecryptPublicKey(self):
        if not self.PublicKeyEncrypted:
            return ''
        return _decrypt_field(self.PublicKeyEncrypted)

    @classmethod
    def FromTenantAndPlatform(cls, tenant_uuid, platform):
        try:
            return cls.objects.get(TenantUUID=tenant_uuid, Platform=platform, ConfigStatus=1)
        except cls.DoesNotExist:
            return None


class PaymentTransaction(QModel):
    """支付交易流水表——SAAS 级统一交易记录

    每笔支付/退款/转账操作都在此记录一条流水。
    子服务通过此表的 TransactionUUID 关联自己的业务订单。
    """

    TX_PAY = 'pay'
    TX_REFUND = 'refund'
    TX_TRANSFER = 'transfer'

    TX_TYPE_CHOICES = [
        (TX_PAY, '支付'),
        (TX_REFUND, '退款'),
        (TX_TRANSFER, '转账'),
    ]

    TX_PENDING = 0
    TX_SUCCESS = 1
    TX_FAILED = 2
    TX_CLOSED = 3

    TX_STATUS_CHOICES = [
        (TX_PENDING, '处理中'),
        (TX_SUCCESS, '成功'),
        (TX_FAILED, '失败'),
        (TX_CLOSED, '已关闭'),
    ]

    TransactionUUID = UUIDField(primary_key=True)
    TenantUUID = UUIDField()
    Platform = models.CharField(max_length=32)
    TransactionType = models.CharField(max_length=16, choices=TX_TYPE_CHOICES)
    OutTradeNO = models.CharField(max_length=64, unique=True)
    OutRefundNO = models.CharField(max_length=64, default='', db_index=True)
    ChannelTransactionID = models.CharField(max_length=128, default='', db_index=True)
    TotalAmount = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    Currency = models.CharField(max_length=10, default='CNY')
    TransactionStatus = models.SmallIntegerField(
        default=TX_PENDING, choices=TX_STATUS_CHOICES,
    )
    Subject = models.CharField(max_length=256, default='')
    RawRequest = models.JSONField(default=dict)
    RawResponse = models.JSONField(default=dict)
    NotifyData = models.JSONField(default=dict)
    PayTime = models.DateTimeField(null=True, blank=True)
    CreateTime = models.DateTimeField(default=_timezone.now)
    UpdateTime = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'PaymentTransaction'
        app_label = 'gvsdsdk'
        verbose_name = '支付交易流水'
        verbose_name_plural = '支付交易流水'
        ordering = ['-CreateTime']
        managed = False
        indexes = [
            models.Index(fields=['TenantUUID', 'CreateTime'], name='idx_paytx_tenant_time_sdk'),
            models.Index(fields=['Platform', 'TransactionStatus'], name='idx_paytx_plat_status_sdk'),
            models.Index(fields=['OutTradeNO'], name='idx_paytx_out_trade_no_sdk'),
            models.Index(fields=['OutRefundNO'], name='idx_paytx_out_refund_no_sdk'),
            models.Index(fields=['ChannelTransactionID'], name='idx_paytx_channel_txid_sdk'),
        ]

    def __str__(self):
        return self.OutTradeNO

    @property
    def IsSuccess(self):
        return self.TransactionStatus == self.TX_SUCCESS

    # ─── 状态机：合法转换定义 ─────────────────────────────
    # PENDING → SUCCESS / FAILED / CLOSED
    # SUCCESS → CLOSED（退款后关单）
    # FAILED / CLOSED → 终态，拒绝任何回退
    _VALID_SUCCESS_FROM = {TX_PENDING}
    _VALID_FAILED_FROM = {TX_PENDING}
    _VALID_CLOSED_FROM = {TX_PENDING, TX_SUCCESS}

    def MarkSuccess(self, channel_transaction_id='', pay_time=None, notify_data=None):
        """标记交易成功——仅允许从 PENDING 转换，已成功则幂等返回，终态拒绝回退"""
        # 幂等：已经是成功状态，直接返回（避免重复回调重复写入）
        if self.TransactionStatus == self.TX_SUCCESS:
            return
        # 终态保护：已关闭/已失败的交易不允许回退到成功
        if self.TransactionStatus not in self._VALID_SUCCESS_FROM:
            logger.warning(
                f'拒绝状态回退到 SUCCESS: 当前状态={self.TransactionStatus}, '
                f'交易={self.OutTradeNO}'
            )
            return
        self.TransactionStatus = self.TX_SUCCESS
        self.ChannelTransactionID = channel_transaction_id
        self.PayTime = pay_time or _timezone.now()
        if notify_data:
            self.NotifyData = notify_data
        self.UpdateTime = _timezone.now()
        with transaction.atomic():
            self.save(update_fields=[
                'TransactionStatus', 'ChannelTransactionID',
                'PayTime', 'NotifyData', 'UpdateTime',
            ])

    def MarkFailed(self, raw_response=None):
        """标记交易失败——仅允许从 PENDING 转换，终态拒绝回退"""
        if self.TransactionStatus not in self._VALID_FAILED_FROM:
            logger.warning(
                f'拒绝状态回退到 FAILED: 当前状态={self.TransactionStatus}, '
                f'交易={self.OutTradeNO}'
            )
            return
        self.TransactionStatus = self.TX_FAILED
        if raw_response:
            self.RawResponse = raw_response
        self.UpdateTime = _timezone.now()
        with transaction.atomic():
            self.save(update_fields=['TransactionStatus', 'RawResponse', 'UpdateTime'])

    def MarkClosed(self, notify_data=None):
        """标记交易关闭——允许从 PENDING/SUCCESS 转换，已关闭则幂等返回"""
        # 幂等：已经是关闭状态，直接返回
        if self.TransactionStatus == self.TX_CLOSED:
            return
        if self.TransactionStatus not in self._VALID_CLOSED_FROM:
            logger.warning(
                f'拒绝状态回退到 CLOSED: 当前状态={self.TransactionStatus}, '
                f'交易={self.OutTradeNO}'
            )
            return
        self.TransactionStatus = self.TX_CLOSED
        if notify_data:
            self.NotifyData = notify_data
        self.UpdateTime = _timezone.now()
        with transaction.atomic():
            self.save(update_fields=[
                'TransactionStatus', 'NotifyData', 'UpdateTime',
            ])


def _get_encryption_key():
    from django.conf import settings
    from django.core.exceptions import ImproperlyConfigured
    key = getattr(settings, 'PAYMENT_ENCRYPTION_KEY', None)
    if not key:
        raise ImproperlyConfigured(
            'PAYMENT_ENCRYPTION_KEY 未在 settings 中配置，无法加解密支付密钥'
        )
    return key


def _decrypt_field(encrypted_value):
    from gvsdsdk.auth.crypto import SymmetricDecrypt
    if not encrypted_value:
        return ''
    return SymmetricDecrypt(encrypted_value, _get_encryption_key())


def EncryptField(plain_value):
    from gvsdsdk.auth.crypto import SymmetricEncrypt
    if not plain_value:
        return ''
    return SymmetricEncrypt(plain_value, _get_encryption_key())

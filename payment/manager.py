"""模块：支付管理器
SAAS 级支付编排器——子服务通过此管理器发起支付操作。

职责：
  1. 根据租户UUID + 平台查询 PaymentGatewayConfig
  2. 解密密钥构建 PaymentCredentials
  3. 从网关注册表获取适配器类，创建网关实例
  4. 执行支付操作并记录 PaymentTransaction 流水"""

import uuid
import logging
from decimal import Decimal, InvalidOperation
from django.db import transaction
from django.utils import timezone as _timezone
from gvsdsdk.payment.gateways.base import (
    PaymentCredentials,
    PaymentRequest,
    PaymentResponse,
    RefundRequest,
    RefundResponse,
    RefundQueryResponse,
    TransferRequest,
    TransferResponse,
    QueryResponse,
)
from gvsdsdk.payment.gateways.registry import payment_registry
from gvsdsdk.payment.models import (
    PaymentGatewayConfig,
    PaymentTransaction,
    EncryptField,
)

logger = logging.getLogger(__name__)

# 分→元转换除数（WeChat/Bank 回调金额单位为分，需除以 100 转换为元）
_FEN_TO_YUAN_DIVISOR = Decimal('100')

# 回调金额不一致时允许的误差（元），避免浮点精度导致误报
_AMOUNT_TOLERANCE = Decimal('0.01')


class PaymentError(Exception):
    """支付业务异常——业务逻辑错误（如配置缺失、金额非法、交易不存在）"""

    def __init__(self, message, transaction=None):
        super().__init__(message)
        self.Transaction = transaction


class GatewayError(PaymentError):
    """网关通信异常——网关不可达、验签失败、返回非预期响应等

    继承 PaymentError，因此 except PaymentError 仍可捕获，
    调用方需区分业务错误与网关错误时可单独捕获 GatewayError。
    """


class PaymentManager:
    """支付编排器——SAAS 主服务器核心入口

    用法:
        manager = PaymentManager(tenant_uuid='xxx')

        # 配置支付网关（SAAS 管理员操作）
        manager.ConfigureGateway(
            platform='wechat',
            app_id='wx123',
            mch_id='456',
            api_key='secret',
            notify_url='https://xxx.gvsds.com/api/payment/notify/wechat/',
        )

        # 发起支付
        tx = manager.Pay(
            platform='wechat',
            amount=Decimal('100.00'),
            subject='订单支付',
            method='jsapi',
        )

        # 查询
        manager.Query(out_trade_no='PY-xxx')

        # 退款
        manager.Refund(out_trade_no='PY-xxx', amount=Decimal('100.00'))

        # 回调处理
        manager.HandleNotify(platform='wechat', raw_data=body, headers=headers)
    """

    def __init__(self, tenant_uuid):
        self.TenantUUID = tenant_uuid
        # 配置缓存（M10）：避免同一 PaymentManager 实例内反复查询数据库
        self._ConfigCache: dict = {}
        # 凭证缓存（M11）：避免同一配置反复解密密钥
        self._CredCache: dict = {}

    def ConfigureGateway(self, platform, app_id='', mch_id='',
                         api_key='', private_key='', public_key='',
                         cert_path='', cert_key_path='',
                         notify_url='', return_url='', extra=None, set_default=False):
        """配置支付网关——SAAS 管理员为租户配置支付渠道

        Args:
            platform: 支付平台 ('wechat', 'alipay', 'bank')
            app_id: 应用 ID
            mch_id: 商户号
            api_key: API 密钥（将加密存储）
            private_key: 私钥（将加密存储）
            public_key: 公钥（将加密存储）
            cert_path: 证书路径
            cert_key_path: 证书密钥路径
            notify_url: 回调通知 URL
            return_url: 支付完成跳转 URL
            extra: 额外配置
            set_default: 是否设为默认渠道

        Returns:
            PaymentGatewayConfig 实例
        """
        defaults = {
            'AppID': app_id,
            'MchID': mch_id,
            'APIKeyEncrypted': EncryptField(api_key) if api_key else '',
            'PrivateKeyEncrypted': EncryptField(private_key) if private_key else '',
            'PublicKeyEncrypted': EncryptField(public_key) if public_key else '',
            'CertPath': cert_path,
            'CertKeyPath': cert_key_path,
            'NotifyURL': notify_url,
            'ReturnURL': return_url,
            'ConfigExtra': extra or {},
            'ConfigStatus': 1,
            'UpdateTime': _timezone.now(),
        }
        # 密钥类字段：空值时不覆盖已配置的密钥，避免误清空
        if not api_key:
            defaults.pop('APIKeyEncrypted', None)
        if not private_key:
            defaults.pop('PrivateKeyEncrypted', None)
        if not public_key:
            defaults.pop('PublicKeyEncrypted', None)

        config, created = PaymentGatewayConfig.objects.update_or_create(
            TenantUUID=self.TenantUUID,
            Platform=platform,
            defaults=defaults,
        )

        if set_default:
            PaymentGatewayConfig.objects.filter(
                TenantUUID=self.TenantUUID, ConfigDefault=True,
            ).update(ConfigDefault=False)
            config.ConfigDefault = True
            config.save(update_fields=['ConfigDefault'])

        # 配置变更后失效缓存，确保后续操作使用新配置
        self._ConfigCache.pop(platform, None)
        self._CredCache = {k: v for k, v in self._CredCache.items() if k != platform}

        logger.info(f'支付网关配置: {platform}@tenant={self.TenantUUID}')
        return config

    def Pay(self, platform, amount, subject='', body='',
            method='jsapi', open_id='', client_ip='',
            extra=None, expire_minutes=30):
        """发起支付

        Args:
            platform: 支付平台
            amount: 金额
            subject: 商品标题
            body: 商品描述
            method: 支付方式 (jsapi/app/h5/native/miniapp)
            open_id: 用户 OpenID（JSAPI 必填）
            client_ip: 客户端 IP
            extra: 额外参数
            expire_minutes: 过期分钟数

        Returns:
            PaymentTransaction 实例

        Raises:
            PaymentError: 支付失败
        """
        config = self._GetConfig(platform)
        if config is None:
            raise PaymentError(f'租户未配置支付渠道: {platform}')

        creds = self._build_credentials(config)
        gateway = payment_registry.create(platform, creds)

        out_trade_no = f'PY-{uuid.uuid4().hex[:16].upper()}'
        try:
            total_amount = Decimal(str(amount))
        except (InvalidOperation, ValueError, TypeError):
            raise PaymentError(f'金额格式非法: {amount!r}')
        if total_amount <= 0:
            raise PaymentError('金额必须大于 0')
        req = PaymentRequest(
            OutTradeNO=out_trade_no,
            TotalAmount=total_amount,
            Subject=subject,
            Body=body,
            Method=method,
            OpenID=open_id,
            ClientIP=client_ip,
            ExpireMinutes=expire_minutes,
            Extra=extra or {},
        )

        tx = PaymentTransaction(
            TransactionUUID=uuid.uuid4().bytes,
            TenantUUID=self.TenantUUID,
            Platform=platform,
            TransactionType=PaymentTransaction.TX_PAY,
            OutTradeNO=out_trade_no,
            TotalAmount=total_amount,
            Subject=subject,
            RawRequest={
                'method': method,
                'open_id': open_id,
                'extra': extra or {},
            },
        )
        tx.save()

        try:
            resp = gateway.Pay(req)
        except Exception as e:
            tx.MarkFailed(raw_response={'error': str(e)})
            raise GatewayError('支付发起失败', transaction=tx)

        tx.RawResponse = resp.RawResponse
        tx.UpdateTime = _timezone.now()

        if resp.Success:
            tx.ChannelTransactionID = resp.TransactionID
            tx.save(update_fields=['RawResponse', 'ChannelTransactionID', 'UpdateTime'])
            tx._prepay_info = resp.PayInfo
            return tx
        else:
            tx.MarkFailed()
            raise GatewayError('支付发起失败', transaction=tx)

    def Query(self, out_trade_no, platform=None):
        """查询订单状态

        Args:
            out_trade_no: 商户订单号
            platform: 支付平台（不传则从交易记录推断）

        Returns:
            QueryResponse
        """
        try:
            tx = PaymentTransaction.objects.get(
                OutTradeNO=out_trade_no, TenantUUID=self.TenantUUID,
            )
        except PaymentTransaction.DoesNotExist:
            raise PaymentError(f'交易记录不存在: {out_trade_no}')

        platform = platform or tx.Platform
        config = self._GetConfig(platform)
        if config is None:
            raise PaymentError(f'租户未配置支付渠道: {platform}')

        creds = self._build_credentials(config)
        gateway = payment_registry.create(platform, creds)
        result = gateway.Query(out_trade_no)

        if result.IsPaid and tx.TransactionStatus == PaymentTransaction.TX_PENDING:
            # 仅允许从中间态（PENDING）回退到成功——已关闭/已失败为终态，拒绝回退
            # 注意：PaymentTransaction 仅定义 TX_PENDING/TX_SUCCESS/TX_FAILED/TX_CLOSED
            tx.MarkSuccess(
                channel_transaction_id=result.TransactionID,
                notify_data={'query_result': result.RawResponse},
            )
        elif result.IsRefunded and tx.TransactionStatus != PaymentTransaction.TX_CLOSED:
            tx.MarkClosed(notify_data={'query_result': result.RawResponse})

        return result

    def Refund(self, out_trade_no, amount, reason='', platform=None, extra=None):
        """发起退款

        Args:
            out_trade_no: 商户订单号
            amount: 退款金额
            reason: 退款原因
            platform: 支付平台
            extra: 额外参数

        Returns:
            PaymentTransaction 实例
        """
        out_refund_no = f'RF-{uuid.uuid4().hex[:16].upper()}'
        try:
            amount = Decimal(str(amount))
        except (InvalidOperation, ValueError, TypeError):
            raise PaymentError(f'金额格式非法: {amount!r}')
        if amount <= 0:
            raise PaymentError('金额必须大于 0')

        # 累计退款校验（加行锁防止并发退款竞态）
        from django.db.models import Sum as _Sum
        with transaction.atomic():
            # select_for_update 锁定原始交易行，防止并发退款超额；
            # 同时承担原始交易查询职责，避免事务外无锁查询结果过期
            # H3: 加入 TenantUUID 过滤，防止跨租户访问
            try:
                pay_tx = PaymentTransaction.objects.select_for_update().get(
                    OutTradeNO=out_trade_no,
                    TenantUUID=self.TenantUUID,
                    TransactionType=PaymentTransaction.TX_PAY,
                )
            except PaymentTransaction.DoesNotExist:
                raise PaymentError(f'原始交易不存在: {out_trade_no}')

            if not pay_tx.IsSuccess:
                raise PaymentError(f'原始交易未支付成功，无法退款: {out_trade_no}')

            if amount > pay_tx.TotalAmount:
                raise PaymentError(f'退款金额超过原始交易金额: {amount} > {pay_tx.TotalAmount}')

            platform = platform or pay_tx.Platform
            config = self._GetConfig(platform)
            if config is None:
                raise PaymentError(f'租户未配置支付渠道: {platform}')

            creds = self._build_credentials(config)
            gateway = payment_registry.create(platform, creds)

            # M6: 累计退款校验加入 TenantUUID 防护（跨租户场景下不会累加其他租户退款）
            already_refunded = PaymentTransaction.objects.filter(
                OutTradeNO=out_trade_no,
                TenantUUID=self.TenantUUID,
                TransactionType=PaymentTransaction.TX_REFUND,
                TransactionStatus__in=[
                    PaymentTransaction.TX_SUCCESS,
                    PaymentTransaction.TX_PENDING,
                ],
            ).aggregate(total=_Sum('TotalAmount'))['total'] or Decimal('0')
            if already_refunded + amount > pay_tx.TotalAmount:
                raise PaymentError(f'累计退款金额超过原始交易金额: {already_refunded + amount} > {pay_tx.TotalAmount}')

            req = RefundRequest(
                OutTradeNO=out_trade_no,
                OutRefundNO=out_refund_no,
                TotalAmount=pay_tx.TotalAmount,
                RefundAmount=amount,
                Reason=reason,
                Extra=extra or {},
            )

            rx = PaymentTransaction(
                TransactionUUID=uuid.uuid4().bytes,
                TenantUUID=self.TenantUUID,
                Platform=platform,
                TransactionType=PaymentTransaction.TX_REFUND,
                OutTradeNO=out_trade_no,
                OutRefundNO=out_refund_no,
                TotalAmount=amount,
                RawRequest={'refund_reason': reason, 'extra': extra or {}},
            )
            rx.save()

        try:
            resp = gateway.Refund(req)
        except Exception as e:
            rx.MarkFailed(raw_response={'error': str(e)})
            raise GatewayError('退款发起失败', transaction=rx)

        rx.RawResponse = resp.RawResponse

        if resp.Success:
            rx.MarkSuccess(channel_transaction_id=resp.RefundID)
        else:
            rx.MarkFailed()

        return rx

    def Transfer(self, platform, amount, payee_account='',
                 payee_name='', description='', transfer_type='wallet',
                 extra=None):
        """企业付款到零钱/银行卡

        Args:
            platform: 支付平台
            amount: 转账金额
            payee_account: 收款账户
            payee_name: 收款人姓名
            description: 转账备注
            transfer_type: 转账类型 (wallet/bank)
            extra: 额外参数

        Returns:
            PaymentTransaction 实例
        """
        config = self._GetConfig(platform)
        if config is None:
            raise PaymentError(f'租户未配置支付渠道: {platform}')

        creds = self._build_credentials(config)
        gateway = payment_registry.create(platform, creds)

        out_transfer_no = f'TF-{uuid.uuid4().hex[:16].upper()}'
        try:
            amount = Decimal(str(amount))
        except (InvalidOperation, ValueError, TypeError):
            raise PaymentError(f'金额格式非法: {amount!r}')
        if amount <= 0:
            raise PaymentError('金额必须大于 0')

        req = TransferRequest(
            OutTransferNO=out_transfer_no,
            Amount=amount,
            PayeeAccount=payee_account,
            PayeeName=payee_name,
            Description=description,
            TransferType=transfer_type,
            Extra=extra or {},
        )

        tx = PaymentTransaction(
            TransactionUUID=uuid.uuid4().bytes,
            TenantUUID=self.TenantUUID,
            Platform=platform,
            TransactionType=PaymentTransaction.TX_TRANSFER,
            OutTradeNO=out_transfer_no,
            TotalAmount=amount,
            Subject=description,
            RawRequest={
                'payee_account': payee_account,
                'payee_name': payee_name,
                'transfer_type': transfer_type,
                'extra': extra or {},
            },
        )
        tx.save()

        # H1: 捕获所有异常（不仅是 NotImplementedError），确保网关异常时回写交易状态
        try:
            resp = gateway.Transfer(req)
        except Exception as e:
            tx.MarkFailed(raw_response={'error': str(e)})
            raise GatewayError(str(e), transaction=tx)

        tx.RawResponse = resp.RawResponse
        if resp.Success:
            tx.MarkSuccess(channel_transaction_id=resp.TransferID)
        else:
            tx.MarkFailed()

        return tx

    def HandleNotify(self, platform, raw_data, headers=None):
        """处理支付回调通知

        Args:
            platform: 支付平台
            raw_data: 原始回调数据 (bytes)
            headers: 请求头字典

        Returns:
            PaymentTransaction 实例或 None
        """
        config = self._GetConfig(platform)
        if config is None:
            logger.error(f'租户未配置支付渠道: {platform}')
            return None

        creds = self._build_credentials(config)
        gateway = payment_registry.create(platform, creds)

        if not gateway.VerifyNotify(raw_data, headers or {}):
            logger.warning(f'支付回调签名验证失败: {platform}')
            return None

        notify_data = gateway.ParseNotify(raw_data)
        out_trade_no = notify_data.get('out_trade_no', '')
        if not out_trade_no:
            logger.warning('回调缺少 out_trade_no')
            return None

        # H4: notify_id 幂等去重——防止 Alipay 重放攻击（同一 notify_id 24h 内只处理一次）
        notify_id = notify_data.get('notify_id', '')
        if notify_id:
            try:
                from django.core.cache import cache
                cache_key = f'payment:notify_id:{notify_id}'
                if not cache.add(cache_key, '1', timeout=86400):
                    logger.info(f'回调重复：notify_id={notify_id} 已处理过')
                    try:
                        return PaymentTransaction.objects.get(
                            OutTradeNO=out_trade_no, TenantUUID=self.TenantUUID,
                        )
                    except PaymentTransaction.DoesNotExist:
                        return None
            except Exception as exc:
                logger.warning(f'缓存不可用，跳过 notify_id 去重: {exc}')

        trade_state = notify_data.get('trade_state', '')
        transaction_id = notify_data.get('transaction_id', '')

        # H2: TOCTOU 修复——使用 select_for_update 锁定交易行，防止并发回调重复结算
        # H3: 加入 TenantUUID 过滤，防止跨租户访问
        with transaction.atomic():
            try:
                tx = PaymentTransaction.objects.select_for_update().get(
                    OutTradeNO=out_trade_no, TenantUUID=self.TenantUUID,
                )
            except PaymentTransaction.DoesNotExist:
                logger.warning(f'回调对应交易不存在: {out_trade_no}')
                return None

            if trade_state in ('SUCCESS', 'TRADE_SUCCESS', 'TRADE_FINISHED'):
                # 幂等性检查：已经是成功状态则直接返回，避免重复回调
                if tx.TransactionStatus == PaymentTransaction.TX_SUCCESS:
                    logger.info(f'回调重复：交易 {out_trade_no} 已是成功状态')
                    return tx
                # 终态保护：已关闭的交易不允许回退到成功
                if tx.TransactionStatus == PaymentTransaction.TX_CLOSED:
                    logger.warning(f'回调异常：交易 {out_trade_no} 已关闭，拒绝回退到成功')
                    return tx
                # 金额校验——防止回调金额与原交易不一致
                # 单位差异：WeChat/Bank 回调金额为分（整数），Alipay total_amount 为元（字符串）
                notify_amount = notify_data.get('amount', {}).get('total') or notify_data.get('total_amount')
                if notify_amount is not None:
                    try:
                        notify_decimal = Decimal(str(notify_amount))
                        # WeChat/Bank 回调金额单位为分，需除以 100 转换为元
                        if tx.Platform in ('wechat', 'bank'):
                            notify_decimal = notify_decimal / _FEN_TO_YUAN_DIVISOR
                        if abs(notify_decimal - tx.TotalAmount) > _AMOUNT_TOLERANCE:
                            logger.warning(f'回调金额不一致: {notify_decimal} != {tx.TotalAmount}, 交易 {out_trade_no}')
                            return tx
                    except (ValueError, TypeError, InvalidOperation):
                        pass
                tx.MarkSuccess(
                    channel_transaction_id=transaction_id,
                    notify_data=notify_data,
                )
            elif trade_state in ('CLOSED', 'REVOKED', 'TRADE_CLOSED'):
                tx.MarkClosed(notify_data=notify_data)
            elif trade_state in ('REFUND', 'TRADE_REFUND'):
                # 幂等性检查：已经是关闭状态则直接返回（退款回调可能重复推送）
                if tx.TransactionStatus == PaymentTransaction.TX_CLOSED:
                    logger.info(f'回调重复：交易 {out_trade_no} 已是关闭/退款状态')
                    return tx
                # 终态保护：已成功的交易退款后回退到关闭，允许该转换
                tx.MarkClosed(notify_data=notify_data)
                # 更新对应的退款记录状态——按 out_refund_no 查找 TX_REFUND 记录
                # 回退兼容：Alipay/Bank 退款异步通知使用 out_request_no 字段，WeChat 使用 out_refund_no
                # H3: 加入 TenantUUID 过滤，防止跨租户访问
                out_refund_no = notify_data.get('out_refund_no', '') or notify_data.get('out_request_no', '')
                if out_refund_no:
                    try:
                        refund_tx = PaymentTransaction.objects.select_for_update().get(
                            OutRefundNO=out_refund_no,
                            TenantUUID=self.TenantUUID,
                            TransactionType=PaymentTransaction.TX_REFUND,
                        )
                        if refund_tx.TransactionStatus != PaymentTransaction.TX_SUCCESS:
                            refund_tx.MarkSuccess(
                                channel_transaction_id=transaction_id,
                                notify_data=notify_data,
                            )
                    except PaymentTransaction.DoesNotExist:
                        logger.warning(f'退款记录不存在: {out_refund_no}')

        return tx

    def GetGatewayInstance(self, platform):
        """直接获取网关实例——供子服务自定义操作

        Args:
            platform: 支付平台

        Returns:
            AbstractPaymentGateway 实例

        Raises:
            PaymentError: 租户未配置支付渠道
        """
        config = self._GetConfig(platform)
        if config is None:
            raise PaymentError(f'租户未配置支付渠道: {platform}')

        creds = self._build_credentials(config)
        return payment_registry.create(platform, creds)

    def QueryRefund(self, out_refund_no, platform=None):
        """查询退款状态

        Args:
            out_refund_no: 商户退款单号
            platform: 支付平台（不传则从退款记录推断）

        Returns:
            RefundQueryResponse

        Raises:
            PaymentError: 退款记录不存在或租户未配置渠道
            GatewayError: 网关查询失败
        """
        # H3: 加入 TenantUUID 过滤，防止跨租户访问
        try:
            tx = PaymentTransaction.objects.get(
                OutRefundNO=out_refund_no, TenantUUID=self.TenantUUID,
            )
        except PaymentTransaction.DoesNotExist:
            raise PaymentError(f'退款记录不存在: {out_refund_no}')

        platform = platform or tx.Platform
        config = self._GetConfig(platform)
        if config is None:
            raise PaymentError(f'租户未配置支付渠道: {platform}')

        creds = self._build_credentials(config)
        gateway = payment_registry.create(platform, creds)

        try:
            return gateway.QueryRefund(out_refund_no)
        except NotImplementedError as e:
            raise PaymentError(str(e), transaction=tx)
        except Exception as e:
            raise GatewayError(f'退款查询失败: {e}', transaction=tx)

    def _GetConfig(self, platform):
        """获取支付网关配置——带实例级缓存（M10），避免同一实例内反复查询数据库"""
        if platform not in self._ConfigCache:
            self._ConfigCache[platform] = PaymentGatewayConfig.FromTenantAndPlatform(
                self.TenantUUID, platform
            )
        return self._ConfigCache[platform]

    def _build_credentials(self, config):
        """从数据库配置构建 PaymentCredentials——带实例级缓存（M11），避免反复解密"""
        cache_key = config.Platform
        if cache_key in self._CredCache:
            return self._CredCache[cache_key]
        creds = PaymentCredentials(
            AppID=config.AppID,
            MchID=config.MchID,
            APIKey=config.DecryptAPIKey(),
            PrivateKey=config.DecryptPrivateKey(),
            PublicKey=config.DecryptPublicKey(),
            CertPath=config.CertPath,
            CertKeyPath=config.CertKeyPath,
            NotifyURL=config.NotifyURL,
            ReturnURL=config.ReturnURL,
            Extra=config.ConfigExtra,
        )
        self._CredCache[cache_key] = creds
        return creds

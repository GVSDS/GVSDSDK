"""GVSDS SDK Django AppConfig

使 gvsdsdk 可作为 Django INSTALLED_APPS 注册，以便 Django 发现 SDK 中的模型。
"""

from django.apps import AppConfig


class GvsdsdkConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'gvsdsdk'
    label = 'gvsdsdk'
    verbose_name = 'GVSDS SDK'

    def ready(self):
        # 注册支付网关——确保子服务器独立部署时也能使用支付功能
        # 主服务器 gvsds.apps.payment 也会注册，重复注册会覆盖，无副作用
        import logging
        logger = logging.getLogger('gvsdsdk')
        try:
            from gvsdsdk.payment.gateways.registry import payment_registry
            from gvsdsdk.payment.gateways.wechat import WechatPaymentGateway
            from gvsdsdk.payment.gateways.alipay import AlipayPaymentGateway
            from gvsdsdk.payment.gateways.bank import BankPaymentGateway
        except ImportError:
            # 依赖缺失——网关适配器所需第三方库未安装，调用 Pay() 时才会暴露
            logger.exception('支付网关自动注册失败：依赖缺失，请安装对应网关所需的第三方库')
            return
        try:
            payment_registry.register('wechat', WechatPaymentGateway)
            payment_registry.register('alipay', AlipayPaymentGateway)
            payment_registry.register('bank', BankPaymentGateway)
        except (ValueError, TypeError, AttributeError):
            # 注册逻辑错误——网关适配器实现或注册表异常
            logger.exception('支付网关自动注册失败：注册逻辑发生异常，请检查网关适配器实现')
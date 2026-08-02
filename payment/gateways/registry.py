"""模块：支付网关注册表
网关注册工厂——管理所有支付渠道适配器的注册与发现。
子服务通过注册表获取网关实例，不需要知道具体实现类。"""

from typing import Dict, Type, Optional
from gvsdsdk.payment.gateways.base import (
    AbstractPaymentGateway,
    PaymentCredentials,
    PaymentChannel,
)


class GatewayRegistry:
    """支付网关注册工厂

    用法:
        registry = GatewayRegistry()

        # 注册渠道
        registry.register('wechat', WechatPaymentGateway)

        # 创建网关实例
        gw = registry.create('wechat', credentials)
        resp = gw.Pay(request)
    """

    def __init__(self):
        self._gateways: Dict[str, Type[AbstractPaymentGateway]] = {}

    def register(self, channel: str, gateway_class: Type[AbstractPaymentGateway]):
        """注册支付渠道适配器

        Args:
            channel: 渠道标识（如 'wechat', 'alipay', 'bank'）
            gateway_class: 实现 AbstractPaymentGateway 的适配器类
        """
        if not issubclass(gateway_class, AbstractPaymentGateway):
            raise TypeError(
                f'{gateway_class.__name__} 必须继承 AbstractPaymentGateway'
            )
        self._gateways[channel] = gateway_class

    def unregister(self, channel: str):
        """注销支付渠道"""
        self._gateways.pop(channel, None)

    def create(self, channel: str, credentials: PaymentCredentials) -> AbstractPaymentGateway:
        """根据渠道标识创建网关实例

        Args:
            channel: 渠道标识
            credentials: 支付凭证

        Returns:
            AbstractPaymentGateway 实例

        Raises:
            ValueError: 渠道未注册
        """
        gateway_class = self._gateways.get(channel)
        if gateway_class is None:
            raise ValueError(
                f'未注册的支付渠道: {channel}，可用渠道: {list(self._gateways.keys())}'
            )
        return gateway_class(credentials)

    def list_channels(self):
        """列出所有已注册的支付渠道"""
        return list(self._gateways.keys())

    def get_gateway_info(self, channel: str):
        """获取渠道信息"""
        gateway_class = self._gateways.get(channel)
        if gateway_class is None:
            return None
        return {
            'channel': gateway_class.Channel,
            'name': gateway_class.Name,
        }

    def list_all_info(self):
        """列出所有渠道信息"""
        return [
            {
                'channel': cls.Channel,
                'name': cls.Name,
            }
            for cls in self._gateways.values()
        ]


payment_registry = GatewayRegistry()

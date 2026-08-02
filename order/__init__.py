"""GVSDS SDK 订单模块

提供订单模板（OrderTemplate）和订单（Order）的 SAAS 级数据模型。
子服务可基于模板快速创建订单，可选绑定支付网关和等级设置。

模型：
    OrderTemplate     — 订单模板（定义订单结构、等级、支付配置）
    OrderTemplateItem — 订单模板项（模板中的行项目定义）
    OrderLevel        — 订单等级（如 VIP、SVIP 等定价阶梯）
    Order             — 订单实例
    OrderItem         — 订单行项目

管理器：
    OrderManager      — 订单 OOP 编排器（创建/状态流转/查询）
    OrderError        — 订单业务异常
"""

from gvsdsdk.order.models import (
    OrderLevel,
    OrderTemplate,
    OrderTemplateItem,
    Order,
    OrderItem,
)
from gvsdsdk.order.manager import OrderManager, OrderError

__all__ = [
    'OrderLevel',
    'OrderTemplate',
    'OrderTemplateItem',
    'Order',
    'OrderItem',
    'OrderManager',
    'OrderError',
]

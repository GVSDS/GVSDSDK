"""gvsdsdk.payment.gateways — 支付网关适配器"""


def __getattr__(name):
    if name in ('AbstractPaymentGateway', 'PaymentCredentials'):
        from gvsdsdk.payment.gateways import base as _m
        val = getattr(_m, name)
        globals()[name] = val
        return val
    if name in ('GatewayRegistry', 'payment_registry'):
        from gvsdsdk.payment.gateways import registry as _m
        val = getattr(_m, name)
        globals()[name] = val
        return val
    raise AttributeError(f"module 'gvsdsdk.payment.gateways' has no attribute {name!r}")

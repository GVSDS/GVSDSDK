"""gvsdsdk.payment — 支付网关"""

__all__ = [
    'PaymentGatewayConfig', 'PaymentTransaction', 'EncryptField',
    'PaymentManager', 'PaymentError', 'GatewayError',
    'PaymentCredentials', 'PaymentRequest', 'PaymentResponse',
    'RefundRequest', 'RefundResponse', 'RefundQueryResponse',
    'TransferRequest', 'TransferResponse',
    'QueryResponse', 'AbstractPaymentGateway',
    'GatewayRegistry', 'payment_registry',
    'WechatPaymentGateway', 'AlipayPaymentGateway', 'BankPaymentGateway',
]


def __getattr__(name):
    if name in ('PaymentGatewayConfig', 'PaymentTransaction', 'EncryptField'):
        from gvsdsdk.payment import models as _m
        val = getattr(_m, name)
        globals()[name] = val
        return val
    if name in ('PaymentManager', 'PaymentError', 'GatewayError'):
        from gvsdsdk.payment import manager as _m
        val = getattr(_m, name)
        globals()[name] = val
        return val
    if name in ('PaymentCredentials', 'PaymentRequest', 'PaymentResponse', 'RefundRequest', 'RefundResponse', 'RefundQueryResponse', 'TransferRequest', 'TransferResponse', 'QueryResponse', 'AbstractPaymentGateway'):
        from gvsdsdk.payment.gateways import base as _m
        val = getattr(_m, name)
        globals()[name] = val
        return val
    if name in ('GatewayRegistry', 'payment_registry'):
        from gvsdsdk.payment.gateways import registry as _m
        val = getattr(_m, name)
        globals()[name] = val
        return val
    if name == 'WechatPaymentGateway':
        from gvsdsdk.payment.gateways.wechat import WechatPaymentGateway
        globals()['WechatPaymentGateway'] = WechatPaymentGateway
        return WechatPaymentGateway
    if name == 'AlipayPaymentGateway':
        from gvsdsdk.payment.gateways.alipay import AlipayPaymentGateway
        globals()['AlipayPaymentGateway'] = AlipayPaymentGateway
        return AlipayPaymentGateway
    if name == 'BankPaymentGateway':
        from gvsdsdk.payment.gateways.bank import BankPaymentGateway
        globals()['BankPaymentGateway'] = BankPaymentGateway
        return BankPaymentGateway
    raise AttributeError(f"module 'gvsdsdk.payment' has no attribute {name!r}")

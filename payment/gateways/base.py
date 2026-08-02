"""模块：抽象支付网关
SAAS 支付网关抽象层——不绑定具体业务，只定义支付操作的通用契约。

架构设计：
  子服务（如阿龙电竞）
    │
    ├── 注入租户密钥 → PaymentGatewayConfig
    │
    └── 调用 PaymentManager.pay(...)
            │
            ├── 根据 platform 类型获取对应的 GatewayAdapter
            ├── 网关实例由密钥创建（非来自数据库）
            └── 执行 Pay / Query / Refund / Close

设计原则：
  1. 网关是无状态的，凭据由调用方在构造时传入
  2. 不关心"谁"在付款（那是子服务的事）
  3. 只关心"通过哪个渠道"付了"多少钱"
  4. SAAS 主服务器只记录交易流水，不记录业务上下文"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Literal
from decimal import Decimal


PaymentChannel = Literal['wechat', 'alipay', 'bank', 'unionpay', 'custom']

PaymentMethod = Literal['jsapi', 'app', 'h5', 'native', 'miniapp', 'scan', 'transfer']


@dataclass
class PaymentCredentials:
    """支付凭证——网关实例创建时传入，不持久化到数据库"""

    AppID: str = ''
    MchID: str = ''
    APIKey: str = ''
    PrivateKey: str = ''
    PublicKey: str = ''
    CertPath: str = ''
    CertKeyPath: str = ''
    NotifyURL: str = ''
    ReturnURL: str = ''
    Extra: Dict[str, str] = field(default_factory=dict)

    def AsDict(self):
        return {
            'app_id': self.AppID,
            'mch_id': self.MchID,
            'api_key': '***',
            'notify_url': self.NotifyURL,
            'return_url': self.ReturnURL,
            'extra_keys': list(self.Extra.keys()),
        }


@dataclass
class PaymentRequest:
    """支付请求——标准化入参"""

    OutTradeNO: str
    TotalAmount: Decimal
    Currency: str = 'CNY'
    Subject: str = ''
    Body: str = ''
    Method: PaymentMethod = 'jsapi'
    OpenID: str = ''
    ClientIP: str = ''
    ExpireMinutes: int = 30
    Extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PaymentResponse:
    """支付响应——标准化出参"""

    Success: bool
    OutTradeNO: str
    TransactionID: str = ''
    PayInfo: Dict[str, Any] = field(default_factory=dict)
    RawResponse: Dict[str, Any] = field(default_factory=dict)

    @property
    def PrepayID(self):
        return self.PayInfo.get('prepay_id', self.PayInfo.get('prepayid', ''))


@dataclass
class RefundRequest:
    """退款请求"""

    OutTradeNO: str
    OutRefundNO: str
    TotalAmount: Decimal
    RefundAmount: Decimal
    Reason: str = ''
    Extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RefundResponse:
    """退款响应"""

    Success: bool
    OutRefundNO: str
    RefundID: str = ''
    RawResponse: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RefundQueryResponse:
    """退款查询响应——用于 QueryRefund 接口"""

    Success: bool
    OutRefundNO: str
    RefundID: str = ''
    RefundStatus: str = ''
    RefundAmount: Decimal = Decimal('0')
    RawResponse: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TransferRequest:
    """转账请求（企业付款到零钱/银行卡）"""

    OutTransferNO: str
    Amount: Decimal
    PayeeAccount: str = ''
    PayeeName: str = ''
    Description: str = ''
    TransferType: Literal['wallet', 'bank'] = 'wallet'
    Extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TransferResponse:
    """转账响应"""

    Success: bool
    OutTransferNO: str
    TransferID: str = ''
    RawResponse: Dict[str, Any] = field(default_factory=dict)


@dataclass
class QueryResponse:
    """订单查询响应"""

    Success: bool
    OutTradeNO: str
    TransactionID: str = ''
    TradeState: str = ''
    TradeStateDesc: str = ''
    TotalAmount: Decimal = Decimal('0')
    RawResponse: Dict[str, Any] = field(default_factory=dict)

    @property
    def IsPaid(self):
        return self.TradeState in ('SUCCESS', 'TRADE_SUCCESS', 'TRADE_FINISHED')

    @property
    def IsRefunded(self):
        return self.TradeState in ('REFUND', 'TRADE_REFUND')


class AbstractPaymentGateway(ABC):
    """抽象支付网关基类

    所有支付渠道的适配器必须实现此接口。
    每个实例绑定一组支付凭证，不同租户/不同平台使用不同实例。

    用法:
        creds = PaymentCredentials(AppID='wx...', MchID='123', APIKey='...')
        gw = WechatPaymentGateway(creds)
        resp = gw.Pay(PaymentRequest(OutTradeNO='X', TotalAmount=Decimal('1.00')))
    """

    Channel: PaymentChannel = 'custom'
    Name: str = 'Abstract'

    def __init__(self, credentials: PaymentCredentials):
        self._credentials = credentials
        self._validate_credentials()

    def _validate_credentials(self):
        pass

    @property
    def Credentials(self):
        return self._credentials

    # ─── 核心支付接口 ──────────────────────────────────────

    @abstractmethod
    def Pay(self, request: PaymentRequest) -> PaymentResponse:
        """发起支付——返回前端调起支付所需参数"""
        ...

    @abstractmethod
    def Query(self, out_trade_no: str) -> QueryResponse:
        """查询订单状态"""
        ...

    @abstractmethod
    def Refund(self, request: RefundRequest) -> RefundResponse:
        """发起退款"""
        ...

    @abstractmethod
    def Close(self, out_trade_no: str) -> bool:
        """关闭未支付的订单"""
        ...

    # ─── 回调处理 ─────────────────────────────────────────

    @abstractmethod
    def VerifyNotify(self, raw_data: bytes, headers: Dict[str, str]) -> bool:
        """验证支付回调签名的合法性"""
        ...

    @abstractmethod
    def ParseNotify(self, raw_data: bytes) -> Dict[str, Any]:
        """解析支付回调数据为标准化字典"""
        ...

    # ─── 可选接口 ─────────────────────────────────────────

    def Transfer(self, request: TransferRequest) -> TransferResponse:
        """企业付款到零钱/银行卡（部分渠道不支持）"""
        raise NotImplementedError(f'{self.Name} 不支持转账功能')

    def QueryTransfer(self, out_transfer_no: str) -> TransferResponse:
        """查询转账状态"""
        raise NotImplementedError(f'{self.Name} 不支持转账查询')

    def QueryRefund(self, out_refund_no: str) -> RefundQueryResponse:
        """查询退款状态——返回退款详情及渠道状态"""
        raise NotImplementedError(f'{self.Name} 不支持退款查询')

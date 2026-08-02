"""模块：银行转账适配器
实现通用银行支付/转账框架——各家银行接口不同，通过可配置 HTTP 端点统一调度。

设计思路：
  Pay / Query / Refund / Transfer 调用 {BANK_API_BASE_URL} 下可配置的子路径，
  以 API Key 头认证，可选 mTLS 双向证书（BANK_CERT_PATH / BANK_CERT_KEY_PATH）。
  请求/响应均为 JSON，由本适配器负责签名头注入、超时重试、错误处理与日志。

凭证来源（运行时配置）：
  优先使用 PaymentCredentials（来自租户配置），缺失时回退到 Django settings。
  所需 settings：
    BANK_API_BASE_URL       银行接口基础地址（如 https://api.example-bank.com/v1）
    BANK_API_KEY            银行分配的 API Key（注入 X-Api-Key 头）
    BANK_CERT_PATH          客户端证书路径（mTLS，可选）
  可选 settings：
    BANK_CERT_KEY_PATH      客户端私钥路径（mTLS，与 BANK_CERT_PATH 配套）
    BANK_MERCHANT_NO        商户号（注入请求体 merchant_no，或回退 credentials.MchID）
    BANK_NOTIFY_URL         异步回调地址
    BANK_SIGN_SECRET        签名密钥（用于 HMAC-SHA256 请求签名，注入 X-Signature 头）
    BANK_HTTP_TIMEOUT       HTTP 超时秒数（默认 10）
    BANK_HTTP_RETRIES       HTTP 失败重试次数（默认 2）
    BANK_PAY_PATH           支付端点路径（默认 /pay）
    BANK_QUERY_PATH         查询端点路径（默认 /query）
    BANK_REFUND_PATH        退款端点路径（默认 /refund）
    BANK_TRANSFER_PATH      转账端点路径（默认 /transfer）
    BANK_CLOSE_PATH         关单端点路径（默认 /close）
    BANK_NOTIFY_PATH        回调查询端点路径（默认 /notify）
"""

import hashlib
import hmac
import json
import logging
import time
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional
from urllib.parse import urljoin

import requests

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from gvsdsdk.payment.gateways.base import (
    AbstractPaymentGateway,
    PaymentCredentials,
    PaymentRequest,
    PaymentResponse,
    QueryResponse,
    RefundRequest,
    RefundResponse,
    RefundQueryResponse,
    TransferRequest,
    TransferResponse,
)

logger = logging.getLogger(__name__)


class BankPaymentGateway(AbstractPaymentGateway):
    """银行转账适配器——通用 HTTP 端点框架"""

    Channel = 'bank'
    Name = '银行卡支付'

    def _validate_credentials(self):
        """运行时配置校验——合并 settings 与 PaymentCredentials，缺失则抛 ImproperlyConfigured"""
        self._APIBaseURL = self._ResolveSetting(
            'BANK_API_BASE_URL', self._credentials.Extra.get('api_base_url', '')
        )
        self._APIKey = self._ResolveSetting(
            'BANK_API_KEY', self._credentials.APIKey
        )
        self._CertPath = (
            getattr(settings, 'BANK_CERT_PATH', '')
            or self._credentials.CertPath
        )
        self._CertKeyPath = (
            getattr(settings, 'BANK_CERT_KEY_PATH', '')
            or self._credentials.CertKeyPath
        )
        self._MerchantNo = (
            getattr(settings, 'BANK_MERCHANT_NO', '')
            or self._credentials.MchID
        )
        self._NotifyURL = (
            getattr(settings, 'BANK_NOTIFY_URL', '')
            or self._credentials.NotifyURL
        )
        self._SignSecret = (
            getattr(settings, 'BANK_SIGN_SECRET', '')
            or self._credentials.Extra.get('sign_secret', '')
        )
        self._HTTPTimeout = getattr(settings, 'BANK_HTTP_TIMEOUT', 10)
        self._HTTPRetries = getattr(settings, 'BANK_HTTP_RETRIES', 2)
        # M4: 复用 HTTP 连接池，避免每次请求都建立新连接
        self._Session = requests.Session()
        self._Paths = {
            'pay': getattr(settings, 'BANK_PAY_PATH', '/pay'),
            'query': getattr(settings, 'BANK_QUERY_PATH', '/query'),
            'refund': getattr(settings, 'BANK_REFUND_PATH', '/refund'),
            'transfer': getattr(settings, 'BANK_TRANSFER_PATH', '/transfer'),
            'close': getattr(settings, 'BANK_CLOSE_PATH', '/close'),
            'notify': getattr(settings, 'BANK_NOTIFY_PATH', '/notify'),
        }

    @staticmethod
    def _ResolveSetting(setting_name: str, fallback: str) -> str:
        value = getattr(settings, setting_name, '') or fallback or ''
        if not value:
            raise ImproperlyConfigured(
                f'银行支付配置不完整：请在 Django settings 中配置 {setting_name}，'
                f'或在 PaymentCredentials 中提供对应字段。'
            )
        return value

    # ─── HTTP 请求调度 ─────────────────────────────────────

    def _Request(
        self,
        path_key: str,
        payload: dict,
        method: str = 'POST',
    ) -> Dict[str, Any]:
        """统一 HTTP 请求调度——端点拼接、签名头、超时重试、日志"""
        url = urljoin(self._APIBaseURL.rstrip('/') + '/', self._Paths[path_key].lstrip('/'))
        body = json.dumps(payload, separators=(',', ':'), ensure_ascii=False)
        headers = self._BuildHeaders(body)

        last_error: Optional[Exception] = None
        for attempt in range(self._HTTPRetries + 1):
            try:
                logger.info(
                    f'银行支付请求 [{method}] {url} attempt={attempt + 1}'
                )
                resp = self._Session.request(
                    method,
                    url,
                    data=body.encode('utf-8'),
                    headers=headers,
                    timeout=self._HTTPTimeout,
                    cert=self._BuildCertTuple(),
                )
                logger.info(
                    f'银行支付响应 HTTP={resp.status_code} body_len={len(resp.content)}'
                )
                return self._ParseResponse(resp)
            except requests.RequestException as exc:
                last_error = exc
                logger.warning(
                    f'银行支付请求异常 attempt={attempt + 1}: {exc}'
                )
                if attempt < self._HTTPRetries:
                    time.sleep(0.5 * (attempt + 1))
        raise last_error  # type: ignore[misc]

    def _BuildHeaders(self, body: str) -> Dict[str, str]:
        """构建请求头——API Key 认证 + 可选 HMAC-SHA256 签名"""
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'X-Api-Key': self._APIKey,
            'X-Merchant-No': self._MerchantNo,
            'User-Agent': 'gvsdsdk-payment-bank/1.0',
        }
        if self._SignSecret:
            signature = hmac.new(
                self._SignSecret.encode('utf-8'),
                body.encode('utf-8'),
                hashlib.sha256,
            ).hexdigest()
            headers['X-Signature'] = signature
        return headers

    def _BuildCertTuple(self):
        """构建 mTLS 证书元组（cert, key），未配置则返回 None"""
        if self._CertPath and self._CertKeyPath:
            return (self._CertPath, self._CertKeyPath)
        if self._CertPath:
            return self._CertPath
        return None

    @staticmethod
    def _ParseResponse(resp: requests.Response) -> Dict[str, Any]:
        """解析 JSON 响应，HTTP 非 2xx 或 code 非成功视为失败"""
        try:
            data = resp.json() if resp.content else {}
        except ValueError:
            data = {'raw_text': resp.text}
        http_ok = 200 <= resp.status_code < 300
        # 约定银行接口返回 code=0 或 code='SUCCESS' 表示成功
        biz_code = str(data.get('code', data.get('status', '')))
        biz_ok = biz_code in ('0', 'SUCCESS', '00')
        data['__http_ok'] = http_ok
        data['__success'] = http_ok and biz_ok
        if not http_ok:
            data['__http_status'] = resp.status_code
        return data

    def _BuildBasePayload(self, request: PaymentRequest) -> dict:
        return {
            'merchant_no': self._MerchantNo,
            'out_trade_no': request.OutTradeNO,
            'total_amount': str(request.TotalAmount),
            'currency': request.Currency or 'CNY',
            'subject': request.Subject or '订单支付',
            'body': request.Body or '',
            'method': request.Method or 'native',
            'client_ip': request.ClientIP or '',
            'expire_minutes': request.ExpireMinutes,
            'notify_url': self._NotifyURL,
        }

    # ─── 支付接口实现 ─────────────────────────────────────

    def Pay(self, request: PaymentRequest) -> PaymentResponse:
        method = request.Method or 'native'
        if method not in ('app', 'h5', 'native'):
            return PaymentResponse(
                Success=False,
                OutTradeNO=request.OutTradeNO,
                RawResponse={'error': f'不支持的支付方式: {method}'},
            )
        payload = self._BuildBasePayload(request)
        if request.Extra:
            payload.update(request.Extra)
        result = self._Request('pay', payload)

        if result.get('__success'):
            pay_info = {
                'tn': result.get('tn', result.get('trade_no', '')),
                'pay_url': result.get('pay_url', ''),
                'method': method,
            }
            return PaymentResponse(
                Success=True,
                OutTradeNO=request.OutTradeNO,
                TransactionID=result.get('transaction_id', ''),
                PayInfo=pay_info,
                RawResponse=result,
            )
        return PaymentResponse(
            Success=False,
            OutTradeNO=request.OutTradeNO,
            RawResponse=result,
        )

    def Query(self, out_trade_no: str) -> QueryResponse:
        payload = {
            'merchant_no': self._MerchantNo,
            'out_trade_no': out_trade_no,
        }
        result = self._Request('query', payload, method='GET')

        if result.get('__success'):
            trade_state = result.get('trade_state', result.get('status', ''))
            total = result.get('total_amount', '0')
            try:
                amount = Decimal(str(total))
            except (InvalidOperation, TypeError, ValueError):
                amount = Decimal('0')
            return QueryResponse(
                Success=True,
                OutTradeNO=out_trade_no,
                TransactionID=result.get('transaction_id', result.get('query_id', '')),
                TradeState=trade_state,
                TradeStateDesc=result.get('trade_state_desc', trade_state),
                TotalAmount=amount,
                RawResponse=result,
            )
        return QueryResponse(
            Success=False,
            OutTradeNO=out_trade_no,
            RawResponse=result,
        )

    def Refund(self, request: RefundRequest) -> RefundResponse:
        payload = {
            'merchant_no': self._MerchantNo,
            'out_trade_no': request.OutTradeNO,
            'out_refund_no': request.OutRefundNO,
            'total_amount': str(request.TotalAmount),
            'refund_amount': str(request.RefundAmount),
            'reason': request.Reason or '',
        }
        if request.Extra:
            payload.update(request.Extra)
        result = self._Request('refund', payload)

        if result.get('__success'):
            return RefundResponse(
                Success=True,
                OutRefundNO=request.OutRefundNO,
                RefundID=result.get('refund_id', result.get('transaction_id', '')),
                RawResponse=result,
            )
        return RefundResponse(
            Success=False,
            OutRefundNO=request.OutRefundNO,
            RawResponse=result,
        )

    def QueryRefund(self, out_refund_no: str) -> RefundQueryResponse:
        """查询退款状态——使用退款端点 GET 请求"""
        payload = {
            'merchant_no': self._MerchantNo,
            'out_refund_no': out_refund_no,
        }
        result = self._Request('refund', payload, method='GET')
        if result.get('__success'):
            try:
                refund_amount = Decimal(str(result.get('refund_amount', '0')))
            except (InvalidOperation, TypeError, ValueError):
                refund_amount = Decimal('0')
            return RefundQueryResponse(
                Success=True,
                OutRefundNO=out_refund_no,
                RefundID=result.get('refund_id', result.get('transaction_id', '')),
                RefundStatus=result.get('refund_status', result.get('trade_state', '')),
                RefundAmount=refund_amount,
                RawResponse=result,
            )
        return RefundQueryResponse(
            Success=False,
            OutRefundNO=out_refund_no,
            RawResponse=result,
        )

    def Close(self, out_trade_no: str) -> bool:
        payload = {
            'merchant_no': self._MerchantNo,
            'out_trade_no': out_trade_no,
        }
        try:
            result = self._Request('close', payload)
            return bool(result.get('__success'))
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning(f'银行关单失败: {exc}')
            return False

    def Transfer(self, request: TransferRequest) -> TransferResponse:
        """银行代付——仅支持付款到银行卡"""
        if request.TransferType != 'bank':
            return TransferResponse(
                Success=False,
                OutTransferNO=request.OutTransferNO,
                RawResponse={'error': '银行支付仅支持付款到银行卡(bank)'},
            )
        payload = {
            'merchant_no': self._MerchantNo,
            'out_transfer_no': request.OutTransferNO,
            'amount': str(request.Amount),
            'payee_account': request.PayeeAccount,
            'payee_name': request.PayeeName,
            'description': request.Description or '代付',
            'transfer_type': 'bank',
            'bank_code': request.Extra.get('bank_code', '') if request.Extra else '',
            'bank_name': request.Extra.get('bank_name', '') if request.Extra else '',
        }
        if request.Extra:
            payload.update(request.Extra)
        result = self._Request('transfer', payload)

        if result.get('__success'):
            return TransferResponse(
                Success=True,
                OutTransferNO=request.OutTransferNO,
                TransferID=result.get('transfer_id', result.get('transaction_id', '')),
                RawResponse=result,
            )
        return TransferResponse(
            Success=False,
            OutTransferNO=request.OutTransferNO,
            RawResponse=result,
        )

    def QueryTransfer(self, out_transfer_no: str) -> TransferResponse:
        payload = {
            'merchant_no': self._MerchantNo,
            'out_transfer_no': out_transfer_no,
        }
        result = self._Request('transfer', payload, method='GET')
        if result.get('__success'):
            return TransferResponse(
                Success=True,
                OutTransferNO=out_transfer_no,
                TransferID=result.get('transfer_id', ''),
                RawResponse=result,
            )
        return TransferResponse(
            Success=False,
            OutTransferNO=out_transfer_no,
            RawResponse=result,
        )

    # ─── 回调处理 ─────────────────────────────────────────

    def VerifyNotify(self, raw_data: bytes, headers: Dict[str, str]) -> bool:
        """验证银行回调签名——使用 BANK_SIGN_SECRET HMAC-SHA256 验证 X-Signature 头"""
        if not raw_data:
            return False
        if not self._SignSecret:
            logger.warning('银行支付未配置 BANK_SIGN_SECRET，拒绝回调')
            return False
        expected = hmac.new(
            self._SignSecret.encode('utf-8'),
            raw_data,
            hashlib.sha256,
        ).hexdigest()
        signature = (
            headers.get('X-Signature', '')
            or headers.get('x-signature', '')
        )
        if not signature:
            return False
        return hmac.compare_digest(expected, signature)

    def ParseNotify(self, raw_data: bytes) -> Dict[str, Any]:
        """解析银行回调——支持 JSON 与 form-urlencoded 两种格式"""
        try:
            body = raw_data.decode('utf-8') if isinstance(raw_data, (bytes, bytearray)) else str(raw_data)
            # 优先尝试 JSON
            try:
                params = json.loads(body)
                if not isinstance(params, dict):
                    params = {}
            except ValueError:
                # 回退到 form-urlencoded
                import urllib.parse as _urlparse
                params = dict(_urlparse.parse_qsl(body))
            return {
                'out_trade_no': params.get('out_trade_no', params.get('orderId', '')),
                # 部分银行/银联渠道退款异步通知使用 out_request_no 字段，需回退兼容
                'out_refund_no': params.get('out_refund_no', params.get('out_request_no', '')),
                'transaction_id': params.get('transaction_id', params.get('queryId', '')),
                'trade_state': params.get('trade_state', params.get('respCode', '')),
                'total_amount': params.get('total_amount', params.get('txnAmt', '0')),
                'settle_date': params.get('settle_date', params.get('settleDate', '')),
                'raw': params,
            }
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning(f'银行回调解析失败: {exc}')
            return {'out_trade_no': '', 'error': f'回调解析失败: {exc}'}

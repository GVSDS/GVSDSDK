"""模块：支付宝适配器
实现支付宝开放平台真实接口：APP / 网页 / 手机网站 支付、查询、退款、转账。

签名算法：RSA2（SHA256WithRSA）—— 商户私钥签名、支付宝公钥验签。
Pay 返回签名后的 order_string（前端跳转/调起参数），不直接发起 HTTP 创建订单。
Query / Refund / Transfer 通过 HTTP POST 调用支付宝网关 gateway.do。

凭证来源（运行时配置）：
  优先使用 PaymentCredentials（来自租户配置），缺失时回退到 Django settings。
  所需 settings：
    ALIPAY_APP_ID            应用 APPID
    ALIPAY_PRIVATE_KEY_PATH  应用私钥文件路径（PEM），或在 credentials.PrivateKey 提供内容
    ALIPAY_PUBLIC_KEY_PATH   支付宝公钥文件路径（PEM），或在 credentials.PublicKey 提供内容
    ALIPAY_GATEWAY_URL       网关地址（默认 https://openapi.alipay.com/gateway.do）
    ALIPAY_NOTIFY_URL        异步回调地址
  可选 settings：
    ALIPAY_RETURN_URL        同步跳转地址
    ALIPAY_SIGN_TYPE         签名类型（默认 RSA2）
    ALIPAY_HTTP_TIMEOUT      HTTP 超时秒数（默认 10）
    ALIPAY_HTTP_RETRIES      HTTP 失败重试次数（默认 2）
"""

import base64
import json
import logging
import time
from datetime import datetime as _dt, timezone as _tz, timedelta as _timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote

import requests
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

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


class AlipayPaymentGateway(AbstractPaymentGateway):
    """支付宝支付适配器——RSA2 真实实现"""

    Channel = 'alipay'
    Name = '支付宝'

    DEFAULT_GATEWAY = 'https://openapi.alipay.com/gateway.do'

    # 回调时间戳新鲜度窗口（秒），超出则判定为重放攻击
    _NOTIFY_TIME_WINDOW_SECONDS = 300

    def _validate_credentials(self):
        """运行时配置校验——合并 settings 与 PaymentCredentials，缺失则抛 ImproperlyConfigured"""
        self._AppID = self._ResolveSetting('ALIPAY_APP_ID', self._credentials.AppID)
        self._PrivateKeyObj = self._LoadKey(
            'ALIPAY_PRIVATE_KEY_PATH', self._credentials.PrivateKey, is_private=True
        )
        self._PublicKeyObj = self._LoadKey(
            'ALIPAY_PUBLIC_KEY_PATH', self._credentials.PublicKey, is_private=False
        )
        self._GatewayURL = (
            getattr(settings, 'ALIPAY_GATEWAY_URL', '')
            or self.DEFAULT_GATEWAY
        )
        self._NotifyURL = (
            getattr(settings, 'ALIPAY_NOTIFY_URL', '')
            or self._credentials.NotifyURL
        )
        self._ReturnURL = (
            getattr(settings, 'ALIPAY_RETURN_URL', '')
            or self._credentials.ReturnURL
        )
        self._SignType = getattr(settings, 'ALIPAY_SIGN_TYPE', 'RSA2')
        self._HTTPTimeout = getattr(settings, 'ALIPAY_HTTP_TIMEOUT', 10)
        self._HTTPRetries = getattr(settings, 'ALIPAY_HTTP_RETRIES', 2)
        # M4: 复用 HTTP 连接池，避免每次请求都建立新连接
        self._Session = requests.Session()

    @staticmethod
    def _ResolveSetting(setting_name: str, fallback: str) -> str:
        value = getattr(settings, setting_name, '') or fallback or ''
        if not value:
            raise ImproperlyConfigured(
                f'支付宝配置不完整：请在 Django settings 中配置 {setting_name}，'
                f'或在 PaymentCredentials 中提供对应字段。'
            )
        return value

    def _LoadKey(self, path_setting: str, content: str, is_private: bool):
        """加载 RSA 密钥——优先 credentials 内容，否则读 settings 路径"""
        if content:
            return self._ParseKeyPEM(content, is_private)
        path = getattr(settings, path_setting, '') or (
            self._credentials.CertKeyPath if is_private else self._credentials.CertPath
        )
        if not path:
            raise ImproperlyConfigured(
                f'支付宝配置不完整：请在 settings 中配置 {path_setting}，'
                f'或在 PaymentCredentials 中提供对应密钥内容。'
            )
        try:
            pem = Path(path).read_text(encoding='utf-8')
        except OSError as exc:
            raise ImproperlyConfigured(f'支付宝密钥文件读取失败: {path} ({exc})')
        return self._ParseKeyPEM(pem, is_private)

    @staticmethod
    def _ParseKeyPEM(key_str, is_private: bool):
        if isinstance(key_str, bytes):
            key_str = key_str.decode('utf-8')
        marker = 'PRIVATE KEY' if is_private else 'PUBLIC KEY'
        if f'-----BEGIN {marker}' not in key_str:
            key_str = (
                f'-----BEGIN {marker}-----\n'
                + key_str.strip()
                + f'\n-----END {marker}-----'
            )
        if is_private:
            return serialization.load_pem_private_key(
                key_str.encode('utf-8'), password=None
            )
        return serialization.load_pem_public_key(key_str.encode('utf-8'))

    # ─── 签名与验签 ─────────────────────────────────────

    @staticmethod
    def _BuildSignContent(params: dict) -> str:
        """构建待签名字符串：按 key 字典序，排除 sign/sign_type，复杂值 JSON 序列化"""
        sorted_keys = sorted(k for k in params if k not in ('sign', 'sign_type'))
        parts = []
        for k in sorted_keys:
            v = params[k]
            if v is None or v == '':
                continue
            if isinstance(v, (dict, list)):
                v = json.dumps(v, separators=(',', ':'), ensure_ascii=False)
            parts.append(f'{k}={v}')
        return '&'.join(parts)

    def _Sign(self, content: str) -> str:
        """RSA2 (SHA256WithRSA) 签名，返回 Base64"""
        signature = self._PrivateKeyObj.sign(
            content.encode('utf-8'),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode('ascii')

    def _Verify(self, content: str, sign_b64: str) -> bool:
        """使用支付宝公钥验证 RSA2 签名"""
        try:
            signature = base64.b64decode(sign_b64)
            self._PublicKeyObj.verify(
                signature,
                content.encode('utf-8'),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
            return True
        except (InvalidSignature, ValueError, TypeError) as exc:
            logger.warning(f'支付宝验签失败: {exc}')
            return False

    def _BuildCommonParams(self, method: str) -> dict:
        """构建支付宝公共请求参数"""
        # 支付宝期望北京时间（Asia/Shanghai）
        # 使用明确的 UTC 时间，不依赖 Django USE_TZ 设置
        try:
            from zoneinfo import ZoneInfo
            now_shanghai = _dt.now(_tz.utc).astimezone(ZoneInfo('Asia/Shanghai'))
            timestamp = now_shanghai.strftime('%Y-%m-%d %H:%M:%S')
        except (ImportError, KeyError):
            # 回退方案：UTC + 8 小时
            now_utc = _dt.now(_tz.utc)
            now_local = now_utc + _timedelta(hours=8)
            timestamp = now_local.strftime('%Y-%m-%d %H:%M:%S')
        return {
            'app_id': self._AppID,
            'method': method,
            'charset': 'utf-8',
            'sign_type': self._SignType,
            'timestamp': timestamp,
            'version': '1.0',
            'biz_content': '',
        }

    def _SignParams(self, params: dict) -> dict:
        """对参数签名并写入 sign 字段"""
        sign_content = self._BuildSignContent(params)
        params['sign'] = self._Sign(sign_content)
        return params

    # ─── HTTP 请求调度 ─────────────────────────────────────

    def _Invoke(self, method: str, biz_content: dict, extra: Optional[dict] = None) -> Dict[str, Any]:
        """统一调用支付宝网关——签名、HTTP POST、响应验签、超时重试"""
        params = self._BuildCommonParams(method)
        params['biz_content'] = json.dumps(
            biz_content, separators=(',', ':'), ensure_ascii=False
        )
        if extra:
            params.update(extra)
        self._SignParams(params)

        last_error: Optional[Exception] = None
        for attempt in range(self._HTTPRetries + 1):
            try:
                logger.info(
                    f'支付宝请求 method={method} attempt={attempt + 1}'
                )
                resp = self._Session.post(
                    self._GatewayURL,
                    data=params,
                    timeout=self._HTTPTimeout,
                )
                logger.info(
                    f'支付宝响应 HTTP={resp.status_code} body_len={len(resp.content)}'
                )
                return self._ParseGatewayResponse(resp, method)
            except requests.RequestException as exc:
                last_error = exc
                logger.warning(
                    f'支付宝请求异常 attempt={attempt + 1}: {exc}'
                )
                if attempt < self._HTTPRetries:
                    time.sleep(min(8.0, 0.5 * (2 ** attempt)))
        raise last_error  # type: ignore[misc]

    def _ParseGatewayResponse(self, resp: requests.Response, method: str) -> Dict[str, Any]:
        """解析网关响应——JSON 转 flat dict，验证 sign"""
        try:
            data = resp.json()
        except ValueError:
            return {'__success': False, 'raw_text': resp.text}

        response_key = f'{method.replace(".", "_")}_response'
        body = data.get(response_key, {})
        sign = data.get('sign', '')

        # 验签：优先从原始响应文本提取签名内容（稳健），回退到重新序列化
        if sign and body:
            sign_content = self._ExtractSignContent(resp.text, response_key)
            if sign_content is None:
                # 回退：重新序列化（多数情况支付宝用紧凑 JSON 可匹配）
                sign_content = json.dumps(
                    body, separators=(',', ':'), ensure_ascii=False
                )
            body['__sign_verified'] = self._Verify(sign_content, sign)
        else:
            body['__sign_verified'] = False
            body['__sign_error'] = 'sign missing' if not sign else 'body empty'

        # 验签失败强制失败——防止伪造响应绕过验签
        if not body.get('__sign_verified'):
            body['__success'] = False
            body['__http_status'] = resp.status_code
            if '__sign_error' not in body:
                body['__sign_error'] = 'sign verify failed'
            return body

        if body.get('code', '') in ('10000',):
            body['__success'] = True
        else:
            body['__success'] = False
            body['__http_status'] = resp.status_code
        return body

    @staticmethod
    def _ExtractSignContent(raw_text: str, response_key: str) -> Optional[str]:
        r"""从原始响应文本中提取签名内容（response_key 对应的原始 JSON 字符串）

        支付宝签名基于原始响应字节流，重新序列化可能因键序/空格不匹配导致验签失败。
        此方法使用括号配对法从原始文本提取 response_key 对应的完整 JSON 对象字符串，
        正确处理嵌套 JSON 对象（正则的非贪婪匹配 `\{.*?\}` 会在嵌套 `}` 处提前截断）。
        """
        import re as _re
        # 定位 response_key 键名位置
        key_pattern = '"' + _re.escape(response_key) + r'"\s*:\s*\{'
        key_match = _re.search(key_pattern, raw_text)
        if not key_match:
            return None

        # 从 `{` 开始括号配对，正确处理字符串内的 `{}` 和转义字符
        start = key_match.end() - 1  # 指向 `{`
        pos = start
        depth = 0
        in_string = False
        escape = False
        while pos < len(raw_text):
            ch = raw_text[pos]
            if in_string:
                if escape:
                    escape = False
                elif ch == '\\':
                    escape = True
                elif ch == '"':
                    in_string = False
            else:
                if ch == '"':
                    in_string = True
                elif ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        return raw_text[start:pos + 1]
            pos += 1
        return None  # 括号未配对，返回 None 触发回退路径

    @staticmethod
    def _BuildOrderString(params: dict) -> str:
        """构建最终 order_string——所有值 URL 编码"""
        parts = []
        for k in sorted(params.keys()):
            v = params[k]
            if v is None or v == '':
                continue
            if isinstance(v, (dict, list)):
                v = json.dumps(v, separators=(',', ':'), ensure_ascii=False)
            parts.append(f'{k}={quote(str(v), safe="")}')
        return '&'.join(parts)

    # ─── 支付接口实现 ─────────────────────────────────────

    def Pay(self, request: PaymentRequest) -> PaymentResponse:
        method = request.Method or 'app'
        if method not in ('app', 'h5', 'native', 'wap', 'miniapp'):
            return PaymentResponse(
                Success=False,
                OutTradeNO=request.OutTradeNO,
                RawResponse={'error': f'不支持的支付方式: {method}'},
            )

        api_method, product_code = self._ResolveTradeMethod(method)
        biz_content = {
            'out_trade_no': request.OutTradeNO,
            'total_amount': str(request.TotalAmount),
            'subject': request.Subject or '订单支付',
            'body': request.Body or '',
            'product_code': product_code,
            'timeout_express': f'{request.ExpireMinutes}m',
        }
        params = self._BuildCommonParams(api_method)
        params['biz_content'] = biz_content
        if self._NotifyURL:
            params['notify_url'] = self._NotifyURL
        if self._ReturnURL and method in ('h5', 'native', 'wap'):
            params['return_url'] = self._ReturnURL
        self._SignParams(params)

        order_string = self._BuildOrderString(params)
        return PaymentResponse(
            Success=True,
            OutTradeNO=request.OutTradeNO,
            PayInfo={
                'order_string': order_string,
                'method': api_method,
                'product_code': product_code,
            },
            RawResponse={'method': api_method, 'product_code': product_code},
        )

    @staticmethod
    def _ResolveTradeMethod(method: str):
        """支付方式 → (支付宝接口方法, product_code)"""
        if method == 'app':
            return 'alipay.trade.app.pay', 'QUICK_MSECURITY_PAY'
        if method in ('h5', 'wap'):
            return 'alipay.trade.wap.pay', 'FAST_INSTANT_TRADE_PAY'
        if method == 'native':
            return 'alipay.trade.page.pay', 'FAST_INSTANT_TRADE_PAY'
        if method == 'miniapp':
            return 'alipay.trade.app.pay', 'QUICK_MSECURITY_PAY'
        return 'alipay.trade.page.pay', 'FAST_INSTANT_TRADE_PAY'

    def Query(self, out_trade_no: str) -> QueryResponse:
        biz_content = {'out_trade_no': out_trade_no}
        result = self._Invoke('alipay.trade.query', biz_content)

        if result.get('__success'):
            trade_status = result.get('trade_status', '')
            total = result.get('total_amount', '0')
            try:
                amount = Decimal(total)
            except (InvalidOperation, TypeError, ValueError):
                amount = Decimal('0')
            return QueryResponse(
                Success=True,
                OutTradeNO=out_trade_no,
                TransactionID=result.get('trade_no', ''),
                TradeState=trade_status or 'WAIT',
                TradeStateDesc=self._DescribeTradeStatus(trade_status),
                TotalAmount=amount,
                RawResponse=result,
            )
        return QueryResponse(
            Success=False,
            OutTradeNO=out_trade_no,
            RawResponse=result,
        )

    @staticmethod
    def _DescribeTradeStatus(status: str) -> str:
        mapping = {
            'TRADE_SUCCESS': '交易支付成功',
            'TRADE_FINISHED': '交易完成',
            'TRADE_CLOSED': '交易关闭',
            'WAIT_BUYER_PAY': '等待买家付款',
        }
        return mapping.get(status, status or '未知状态')

    def Refund(self, request: RefundRequest) -> RefundResponse:
        biz_content = {
            'out_trade_no': request.OutTradeNO,
            'refund_amount': str(request.RefundAmount),
            'out_request_no': request.OutRefundNO,
            'refund_reason': request.Reason or '',
        }
        if request.Extra:
            biz_content.update(request.Extra)
        result = self._Invoke('alipay.trade.refund', biz_content)

        if result.get('__success') and result.get('fund_change', '') == 'Y':
            return RefundResponse(
                Success=True,
                OutRefundNO=request.OutRefundNO,
                RefundID=result.get('trade_no', request.OutRefundNO),
                RawResponse=result,
            )
        return RefundResponse(
            Success=False,
            OutRefundNO=request.OutRefundNO,
            RawResponse=result,
        )

    def Close(self, out_trade_no: str) -> bool:
        biz_content = {'out_trade_no': out_trade_no}
        result = self._Invoke('alipay.trade.close', biz_content)
        return bool(result.get('__success'))

    def QueryRefund(self, out_refund_no: str) -> RefundQueryResponse:
        """查询退款状态——alipay.trade.fastpay.refund.query"""
        biz_content = {'out_request_no': out_refund_no}
        result = self._Invoke('alipay.trade.fastpay.refund.query', biz_content)
        if result.get('__success'):
            refund_status = result.get('refund_status', '')
            try:
                refund_amount = Decimal(result.get('refund_amount', '0'))
            except (InvalidOperation, TypeError, ValueError):
                refund_amount = Decimal('0')
            return RefundQueryResponse(
                Success=True,
                OutRefundNO=out_refund_no,
                RefundID=result.get('trade_no', ''),
                RefundStatus=refund_status or result.get('code', ''),
                RefundAmount=refund_amount,
                RawResponse=result,
            )
        return RefundQueryResponse(
            Success=False,
            OutRefundNO=out_refund_no,
            RawResponse=result,
        )

    def Transfer(self, request: TransferRequest) -> TransferResponse:
        """单笔转账——alipay.fund.trans.uni.transfer"""
        biz_content = {
            'out_biz_no': request.OutTransferNO,
            'trans_amount': str(request.Amount),
            'product_code': 'TRANS_ACCOUNT_NO_PWD',
            'biz_scene': 'DIRECT_TRANSFER',
            'remark': request.Description or '转账',
            'payee_info': {
                'identity': request.PayeeAccount,
                'identity_type': 'ALIPAY_LOGON_ID',
            },
        }
        if request.PayeeName:
            biz_content['payee_info']['name'] = request.PayeeName
        if request.Extra:
            biz_content.update(request.Extra)
        result = self._Invoke('alipay.fund.trans.uni.transfer', biz_content)

        if result.get('__success') and result.get('status', '') in (
            'SUCCESS', 'ACCEPTED'
        ):
            return TransferResponse(
                Success=True,
                OutTransferNO=request.OutTransferNO,
                TransferID=result.get('order_id', result.get('pay_fund_order_id', '')),
                RawResponse=result,
            )
        return TransferResponse(
            Success=False,
            OutTransferNO=request.OutTransferNO,
            RawResponse=result,
        )

    # ─── 回调处理 ─────────────────────────────────────────

    def VerifyNotify(self, raw_data: bytes, headers: Dict[str, str]) -> bool:
        """验证支付宝异步通知签名——解析 form-urlencoded 参数并验证 sign

        M5: 同时校验 notify_time 新鲜度（5 分钟窗口），防止重放攻击。
        """
        if not raw_data:
            return False
        try:
            import urllib.parse as _urlparse

            params = dict(_urlparse.parse_qsl(raw_data.decode('utf-8')))
            sign = params.get('sign', '')
            if not sign:
                return False
            sign_type = params.get('sign_type', '')
            if sign_type and sign_type.upper() != self._SignType:
                return False
            # M5: 校验 notify_time 新鲜度——支付宝 notify_time 格式 'yyyy-MM-dd HH:mm:ss'（北京时间）
            notify_time_str = params.get('notify_time', '')
            if notify_time_str:
                try:
                    from zoneinfo import ZoneInfo
                    notify_time = _dt.strptime(
                        notify_time_str, '%Y-%m-%d %H:%M:%S'
                    ).replace(tzinfo=ZoneInfo('Asia/Shanghai'))
                    now_utc = _dt.now(_tz.utc)
                    if abs((now_utc - notify_time).total_seconds()) > self._NOTIFY_TIME_WINDOW_SECONDS:
                        logger.warning('[Alipay] 回调 notify_time 超出 5 分钟窗口，疑似重放攻击')
                        return False
                except (ValueError, ImportError, KeyError):
                    # 解析失败时不阻止回调（保守策略——签名验证仍生效）
                    logger.warning(f'[Alipay] notify_time 解析失败，跳过新鲜度校验: {notify_time_str}')
            sign_content = self._BuildSignContent(params)
            return self._Verify(sign_content, sign)
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning(f'支付宝回调验签失败: {exc}')
            return False

    def ParseNotify(self, raw_data: bytes) -> Dict[str, Any]:
        """解析支付宝异步通知——form-urlencoded → 标准化字典"""
        try:
            import urllib.parse as _urlparse

            params = dict(_urlparse.parse_qsl(raw_data.decode('utf-8')))
            return {
                'out_trade_no': params.get('out_trade_no', ''),
                # 支付宝退款异步通知使用 out_request_no 字段（非 out_refund_no），需回退兼容
                'out_refund_no': params.get('out_refund_no', params.get('out_request_no', '')),
                'transaction_id': params.get('trade_no', ''),
                'trade_state': params.get('trade_status', ''),
                'total_amount': params.get('total_amount', '0'),
                'buyer_id': params.get('buyer_id', ''),
                'gmt_payment': params.get('gmt_payment', ''),
                # H4: 提取 notify_id 用于幂等去重（防止重放攻击）
                'notify_id': params.get('notify_id', ''),
                'notify_time': params.get('notify_time', ''),
                'raw': params,
            }
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning(f'支付宝回调解析失败: {exc}')
            return {'out_trade_no': '', 'error': f'回调解析失败: {exc}'}

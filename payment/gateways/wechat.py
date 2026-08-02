"""模块：微信支付适配器
实现微信支付 APIv3 真实接口：JSAPI / Native / H5 / APP / 小程序 支付、查询、退款、转账。

签名算法：RSA-SHA256（SHA256withRSA）—— 商户私钥签名、平台证书验签。
回调验签：使用微信平台证书验证 Wechatpay-Signature 头，AES-GCM 解密 resource。

凭证来源（运行时配置）：
  优先使用 PaymentCredentials（来自租户配置），缺失时回退到 Django settings。
  所需 settings：
    WECHAT_MCH_ID            商户号
    WECHAT_API_V3_KEY        APIv3 密钥（AES-GCM 解密回调 / 平台证书）
    WECHAT_SERIAL_NO         商户 API 证书序列号（用于 Authorization 头）
    WECHAT_PRIVATE_KEY_PATH  商户 API 私钥文件路径（PEM）
    WECHAT_CERT_SERIAL_NO    微信平台证书序列号（可选，未配置则自动拉取）
  可选 settings：
    WECHAT_NOTIFY_URL        默认回调地址
    WECHAT_HTTP_TIMEOUT      HTTP 超时秒数（默认 10）
    WECHAT_HTTP_RETRIES      HTTP 失败重试次数（默认 2）
"""

import base64
import json
import logging
import time
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests
from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.x509 import load_pem_x509_certificate

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


class WechatPaymentGateway(AbstractPaymentGateway):
    """微信支付适配器——APIv3 真实实现"""

    Channel = 'wechat'
    Name = '微信支付'

    WECHAT_API_BASE = 'https://api.mch.weixin.qq.com'

    # 类级缓存——避免每次网关实例化（每次回调）都为空，触发 HTTP 拉取证书
    _PlatformCertCache: Dict[str, str] = {}

    # 回调时间戳新鲜度窗口（秒），超出则判定为重放攻击
    _NOTIFY_TIME_WINDOW_SECONDS = 300

    def _validate_credentials(self):
        """运行时配置校验——合并 settings 与 PaymentCredentials，缺失则抛 ImproperlyConfigured"""
        self._MchID = self._ResolveSetting('WECHAT_MCH_ID', self._credentials.MchID)
        self._APIV3Key = self._ResolveSetting('WECHAT_API_V3_KEY', self._credentials.APIKey)
        self._SerialNo = self._ResolveSetting(
            'WECHAT_SERIAL_NO', self._credentials.Extra.get('serial_no', '')
        )
        self._PrivateKeyObj = self._LoadPrivateKey()
        self._PlatformCertSerial = (
            getattr(settings, 'WECHAT_CERT_SERIAL_NO', '')
            or self._credentials.Extra.get('platform_cert_serial', '')
        )
        self._HTTPTimeout = getattr(settings, 'WECHAT_HTTP_TIMEOUT', 10)
        self._HTTPRetries = getattr(settings, 'WECHAT_HTTP_RETRIES', 2)
        # M4: 复用 HTTP 连接池，避免每次请求都建立新连接
        self._Session = requests.Session()

    @staticmethod
    def _ResolveSetting(setting_name: str, fallback: str) -> str:
        """从 settings 读取配置，缺失时回退到 credentials，两者皆空则抛 ImproperlyConfigured"""
        value = getattr(settings, setting_name, '') or fallback or ''
        if not value:
            raise ImproperlyConfigured(
                f'微信支付配置不完整：请在 Django settings 中配置 {setting_name}，'
                f'或在 PaymentCredentials 中提供对应字段。'
            )
        return value

    def _LoadPrivateKey(self):
        """加载商户 RSA 私钥——优先 credentials.PrivateKey 内容，否则读 settings 路径"""
        if self._credentials.PrivateKey:
            return self._ParsePrivateKeyPEM(self._credentials.PrivateKey)
        path = (
            getattr(settings, 'WECHAT_PRIVATE_KEY_PATH', '')
            or self._credentials.CertKeyPath
        )
        if not path:
            raise ImproperlyConfigured(
                '微信支付配置不完整：请在 settings 中配置 WECHAT_PRIVATE_KEY_PATH，'
                '或在 PaymentCredentials.PrivateKey 中提供私钥内容。'
            )
        try:
            pem = Path(path).read_text(encoding='utf-8')
        except OSError as exc:
            raise ImproperlyConfigured(f'微信支付私钥文件读取失败: {path} ({exc})')
        return self._ParsePrivateKeyPEM(pem)

    @staticmethod
    def _ParsePrivateKeyPEM(pem_str):
        if isinstance(pem_str, bytes):
            pem_str = pem_str.decode('utf-8')
        if '-----BEGIN' not in pem_str:
            pem_str = (
                '-----BEGIN PRIVATE KEY-----\n'
                + pem_str.strip()
                + '\n-----END PRIVATE KEY-----'
            )
        return serialization.load_pem_private_key(
            pem_str.encode('utf-8'), password=None
        )

    # ─── 签名与验签 ─────────────────────────────────────

    def _BuildAuthorization(self, http_method: str, url_path: str, body: str = '') -> str:
        """构建 APIv3 Authorization 头：WECHATPAY2-SHA256-RSA2048 mchid=...,signature=..."""
        timestamp = str(int(time.time()))
        nonce_str = _uuid.uuid4().hex
        sign_message = (
            f'{http_method.upper()}\n{url_path}\n{timestamp}\n{nonce_str}\n{body}\n'
        )
        signature = self._PrivateKeyObj.sign(
            sign_message.encode('utf-8'),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        sign_b64 = base64.b64encode(signature).decode('ascii')
        return (
            'WECHATPAY2-SHA256-RSA2048 '
            f'mchid="{self._MchID}",'
            f'serial_no="{self._SerialNo}",'
            f'nonce_str="{nonce_str}",'
            f'timestamp="{timestamp}",'
            f'signature="{sign_b64}"'
        )

    def _VerifySignature(self, timestamp: str, nonce: str, body: str, signature_b64: str) -> bool:
        """使用微信平台证书（RSA-SHA256）验证回调签名"""
        try:
            cert_pem = self._GetPlatformCertificate()
            if not cert_pem:
                logger.warning('微信平台证书不可用，无法验签')
                return False
            cert = load_pem_x509_certificate(cert_pem.encode('utf-8'))
            public_key = cert.public_key()
            message = f'{timestamp}\n{nonce}\n{body}\n'
            signature = base64.b64decode(signature_b64)
            public_key.verify(
                signature,
                message.encode('utf-8'),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
            return True
        except (InvalidSignature, ValueError, TypeError) as exc:
            logger.warning(f'微信支付回调验签失败: {exc}')
            return False

    def _GetPlatformCertificate(self) -> str:
        """获取微信平台证书 PEM——优先缓存，否则调用 /v3/certificates 拉取并 AES-GCM 解密"""
        # 优先从缓存命中：按配置序列号查找，或未配置序列号时使用已缓存的任意证书
        if self._PlatformCertSerial:
            if self._PlatformCertSerial in self._PlatformCertCache:
                return self._PlatformCertCache[self._PlatformCertSerial]
        elif self._PlatformCertCache:
            # 未配置平台证书序列号时，使用已缓存的任意证书（避免每次都发起 HTTP）
            return next(iter(self._PlatformCertCache.values()))

        url = f'{self.WECHAT_API_BASE}/v3/certificates'
        resp = self._Request('GET', url, '', _verify_response=False)
        if resp.status_code != 200:
            logger.error(
                f'获取微信平台证书失败: HTTP {resp.status_code} {resp.text}'
            )
            return ''
        data = resp.json()
        for cert_item in data.get('data', []):
            serial_no = cert_item.get('serial_no', '')
            encrypt_cert = cert_item.get('encrypt_certificate', {})
            pem = self._DecryptAESCipher(encrypt_cert)
            if pem:
                self._PlatformCertCache[serial_no] = pem
                if (
                    not self._PlatformCertSerial
                    or serial_no == self._PlatformCertSerial
                ):
                    return pem
        if self._PlatformCertCache:
            return next(iter(self._PlatformCertCache.values()))
        return ''

    def _DecryptAESCipher(self, encrypt_data: dict) -> str:
        """使用 APIv3 密钥 AES-GCM 解密（平台证书 / 回调 resource 通用）"""
        try:
            nonce = encrypt_data.get('nonce', '').encode('utf-8')
            associated_data = encrypt_data.get('associated_data', '').encode('utf-8')
            ciphertext = base64.b64decode(encrypt_data.get('ciphertext', ''))
            aesgcm = AESGCM(self._APIV3Key.encode('utf-8'))
            plain = aesgcm.decrypt(nonce, ciphertext, associated_data)
            return plain.decode('utf-8')
        except (InvalidTag, ValueError, UnicodeDecodeError) as exc:
            logger.error(f'AES-GCM 解密失败: {exc}')
            return ''

    # ─── HTTP 请求调度 ─────────────────────────────────────

    def _Request(
        self,
        method: str,
        url: str,
        body: str = '',
        extra_headers: Optional[Dict[str, str]] = None,
        _verify_response: bool = True,
    ) -> requests.Response:
        """统一 HTTP 请求调度：签名头、超时、重试、请求/响应日志

        Args:
            _verify_response: 是否验证同步响应签名。获取平台证书时需传 False
                避免 _VerifySignature → _GetPlatformCertificate → _Request 递归。
        """
        url_path = urlparse(url).path
        authorization = self._BuildAuthorization(method, url_path, body)
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Authorization': authorization,
            'User-Agent': 'gvsdsdk-payment-wechat/1.0',
        }
        if extra_headers:
            headers.update(extra_headers)

        last_error: Optional[Exception] = None
        for attempt in range(self._HTTPRetries + 1):
            try:
                logger.info(
                    f'微信支付请求 [{method}] {url} attempt={attempt + 1}'
                )
                resp = self._Session.request(
                    method,
                    url,
                    data=body.encode('utf-8') if body else b'',
                    headers=headers,
                    timeout=self._HTTPTimeout,
                )
                logger.info(
                    f'微信支付响应 HTTP={resp.status_code} body_len={len(resp.content)}'
                )
                # APIv3 同步响应验签——防止中间人篡改响应体
                if _verify_response and resp.status_code == 200:
                    self._VerifySyncResponse(resp)
                return resp
            except requests.RequestException as exc:
                last_error = exc
                logger.warning(
                    f'微信支付请求异常 attempt={attempt + 1}: {exc}'
                )
                if attempt < self._HTTPRetries:
                    time.sleep(min(8.0, 0.5 * (2 ** attempt)))
        raise last_error  # type: ignore[misc]

    def _VerifySyncResponse(self, resp: requests.Response) -> None:
        """验证 APIv3 同步响应签名——验签失败在响应头标记 __sign_verified=false"""
        sig = resp.headers.get('Wechatpay-Signature', '')
        if not sig:
            # 无签名头（如错误响应）跳过验签
            return
        ts = resp.headers.get('Wechatpay-Timestamp', '')
        nonce = resp.headers.get('Wechatpay-Nonce', '')
        if self._VerifySignature(ts, nonce, resp.text, sig):
            resp.headers['__sign_verified'] = 'true'
        else:
            logger.error('微信支付同步响应验签失败——响应可能被篡改')
            resp.headers['__sign_verified'] = 'false'

    @staticmethod
    def _ParseResponse(resp: requests.Response) -> Dict[str, Any]:
        """解析 JSON 响应，HTTP 非 2xx 时附加状态码标记

        __success 标记设置规则：
        - 验签失败或 HTTP 非 2xx 时设为 False
        - 成功时不显式设置（默认 True），调用方用 `data.get('__success', True) is not False` 检查
          （用 `is not False` 而非 `not data.get(...)` 是为了只拒绝显式 False，允许默认 True）
        """
        try:
            data = resp.json() if resp.content else {}
        except ValueError:
            data = {'raw_text': resp.text}
        if not (200 <= resp.status_code < 300):
            data['__http_status'] = resp.status_code
            data['__success'] = False
        # 同步响应验签失败——强制标记为失败，防止处理被篡改的响应
        if resp.headers.get('__sign_verified') == 'false':
            data['__success'] = False
            data['__sign_error'] = 'sync response signature verification failed'
        return data

    # ─── 支付接口实现 ─────────────────────────────────────

    def Pay(self, request: PaymentRequest) -> PaymentResponse:
        method = request.Method or 'jsapi'
        if method == 'jsapi':
            return self._PayJSAPI(request)
        if method == 'native':
            return self._PayNative(request)
        if method == 'h5':
            return self._PayH5(request)
        if method == 'app':
            return self._PayApp(request)
        if method == 'miniapp':
            return self._PayJSAPI(request)
        return PaymentResponse(
            Success=False,
            OutTradeNO=request.OutTradeNO,
            RawResponse={'error': f'不支持的支付方式: {method}'},
        )

    def _BuildPayPayload(self, request: PaymentRequest) -> dict:
        notify_url = (
            self._credentials.NotifyURL
            or getattr(settings, 'WECHAT_NOTIFY_URL', '')
        )
        return {
            'appid': self._credentials.AppID,
            'mchid': self._MchID,
            'description': request.Subject or '订单支付',
            'out_trade_no': request.OutTradeNO,
            'time_expire': self._FormatExpire(request.ExpireMinutes),
            'notify_url': notify_url,
            'amount': {
                'total': int((request.TotalAmount * 100).to_integral_value()),
                'currency': request.Currency or 'CNY',
            },
        }

    @staticmethod
    def _FormatExpire(expire_minutes: int) -> str:
        # 使用明确的 UTC 时间，不依赖 Django USE_TZ 设置
        expire = datetime.now(timezone.utc) + timedelta(minutes=expire_minutes)
        # 微信 APIv3 期望北京时间（+08:00），将 UTC 转换为 Asia/Shanghai
        try:
            from zoneinfo import ZoneInfo
            expire_shanghai = expire.astimezone(ZoneInfo('Asia/Shanghai'))
            return expire_shanghai.strftime('%Y-%m-%dT%H:%M:%S+08:00')
        except (ImportError, KeyError):
            # 回退方案：手动加 8 小时
            expire_local = expire + timedelta(hours=8)
            return expire_local.strftime('%Y-%m-%dT%H:%M:%S+08:00')

    def _PayJSAPI(self, request: PaymentRequest) -> PaymentResponse:
        if not request.OpenID:
            return PaymentResponse(
                Success=False,
                OutTradeNO=request.OutTradeNO,
                RawResponse={'error': 'JSAPI 支付需要 OpenID'},
            )
        payload = self._BuildPayPayload(request)
        payload['payer'] = {'openid': request.OpenID}
        body = json.dumps(payload, separators=(',', ':'), ensure_ascii=False)
        url = f'{self.WECHAT_API_BASE}/v3/pay/transactions/jsapi'
        resp = self._Request('POST', url, body)
        data = self._ParseResponse(resp)
        if resp.status_code == 200 and 'prepay_id' in data and data.get('__success', True) is not False:
            prepay_id = data['prepay_id']
            pay_info = self._BuildJSAPIPayInfo(prepay_id)
            return PaymentResponse(
                Success=True,
                OutTradeNO=request.OutTradeNO,
                PayInfo=pay_info,
                RawResponse=data,
            )
        return PaymentResponse(
            Success=False,
            OutTradeNO=request.OutTradeNO,
            RawResponse=data,
        )

    def _BuildJSAPIPayInfo(self, prepay_id: str) -> dict:
        """构建 JSAPI/小程序调起支付参数（RSA-SHA256 签名）"""
        app_id = self._credentials.AppID
        timestamp = str(int(time.time()))
        nonce_str = _uuid.uuid4().hex
        package = f'prepay_id={prepay_id}'
        sign_message = f'{app_id}\n{timestamp}\n{nonce_str}\n{package}\n'
        signature = self._PrivateKeyObj.sign(
            sign_message.encode('utf-8'),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        pay_sign = base64.b64encode(signature).decode('ascii')
        return {
            'appId': app_id,
            'timeStamp': timestamp,
            'nonceStr': nonce_str,
            'package': package,
            'signType': 'RSA',
            'paySign': pay_sign,
            'prepay_id': prepay_id,
        }

    def _PayNative(self, request: PaymentRequest) -> PaymentResponse:
        payload = self._BuildPayPayload(request)
        body = json.dumps(payload, separators=(',', ':'), ensure_ascii=False)
        url = f'{self.WECHAT_API_BASE}/v3/pay/transactions/native'
        resp = self._Request('POST', url, body)
        data = self._ParseResponse(resp)
        if resp.status_code == 200 and 'code_url' in data and data.get('__success', True) is not False:
            return PaymentResponse(
                Success=True,
                OutTradeNO=request.OutTradeNO,
                PayInfo={'code_url': data['code_url']},
                RawResponse=data,
            )
        return PaymentResponse(
            Success=False,
            OutTradeNO=request.OutTradeNO,
            RawResponse=data,
        )

    def _PayH5(self, request: PaymentRequest) -> PaymentResponse:
        if not request.ClientIP:
            return PaymentResponse(
                Success=False,
                OutTradeNO=request.OutTradeNO,
                RawResponse={'error': 'H5 支付需要 ClientIP'},
            )
        payload = self._BuildPayPayload(request)
        payload['scene_info'] = {
            'payer_client_ip': request.ClientIP,
            'h5_info': {'type': 'Wap'},
        }
        body = json.dumps(payload, separators=(',', ':'), ensure_ascii=False)
        url = f'{self.WECHAT_API_BASE}/v3/pay/transactions/h5'
        resp = self._Request('POST', url, body)
        data = self._ParseResponse(resp)
        if resp.status_code == 200 and 'h5_url' in data and data.get('__success', True) is not False:
            return PaymentResponse(
                Success=True,
                OutTradeNO=request.OutTradeNO,
                PayInfo={'h5_url': data['h5_url']},
                RawResponse=data,
            )
        return PaymentResponse(
            Success=False,
            OutTradeNO=request.OutTradeNO,
            RawResponse=data,
        )

    def _PayApp(self, request: PaymentRequest) -> PaymentResponse:
        payload = self._BuildPayPayload(request)
        body = json.dumps(payload, separators=(',', ':'), ensure_ascii=False)
        url = f'{self.WECHAT_API_BASE}/v3/pay/transactions/app'
        resp = self._Request('POST', url, body)
        data = self._ParseResponse(resp)
        if resp.status_code == 200 and 'prepay_id' in data and data.get('__success', True) is not False:
            prepay_id = data['prepay_id']
            pay_info = self._BuildAppPayInfo(prepay_id)
            return PaymentResponse(
                Success=True,
                OutTradeNO=request.OutTradeNO,
                PayInfo=pay_info,
                RawResponse=data,
            )
        return PaymentResponse(
            Success=False,
            OutTradeNO=request.OutTradeNO,
            RawResponse=data,
        )

    def _BuildAppPayInfo(self, prepay_id: str) -> dict:
        """构建 APP 调起支付参数（RSA-SHA256 签名）"""
        app_id = self._credentials.AppID
        timestamp = str(int(time.time()))
        nonce_str = _uuid.uuid4().hex
        sign_message = f'{app_id}\n{timestamp}\n{nonce_str}\n{prepay_id}\n'
        signature = self._PrivateKeyObj.sign(
            sign_message.encode('utf-8'),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        pay_sign = base64.b64encode(signature).decode('ascii')
        return {
            'appid': app_id,
            'partnerid': self._MchID,
            'prepayid': prepay_id,
            'package': 'Sign=WXPay',
            'noncestr': nonce_str,
            'timestamp': timestamp,
            'sign': pay_sign,
        }

    def Query(self, out_trade_no: str) -> QueryResponse:
        url = (
            f'{self.WECHAT_API_BASE}/v3/pay/transactions/out-trade-no/'
            f'{out_trade_no}?mchid={self._MchID}'
        )
        resp = self._Request('GET', url, '')
        data = self._ParseResponse(resp)
        if resp.status_code == 200 and data.get('__success', True) is not False:
            amount = data.get('amount', {})
            return QueryResponse(
                Success=True,
                OutTradeNO=out_trade_no,
                TransactionID=data.get('transaction_id', ''),
                TradeState=data.get('trade_state', ''),
                TradeStateDesc=data.get('trade_state_desc', ''),
                TotalAmount=Decimal(amount.get('total', 0)) / Decimal(100),
                RawResponse=data,
            )
        return QueryResponse(
            Success=False,
            OutTradeNO=out_trade_no,
            RawResponse=data,
        )

    def Refund(self, request: RefundRequest) -> RefundResponse:
        payload = {
            'out_trade_no': request.OutTradeNO,
            'out_refund_no': request.OutRefundNO,
            'reason': request.Reason or '',
            'amount': {
                'refund': int((request.RefundAmount * 100).to_integral_value()),
                'total': int((request.TotalAmount * 100).to_integral_value()),
                'currency': 'CNY',
            },
        }
        if request.Extra:
            payload.update(request.Extra)
        body = json.dumps(payload, separators=(',', ':'), ensure_ascii=False)
        url = f'{self.WECHAT_API_BASE}/v3/refund/domestic/refunds'
        resp = self._Request('POST', url, body)
        data = self._ParseResponse(resp)
        if resp.status_code in (200, 201) and 'refund_id' in data and data.get('__success', True) is not False:
            return RefundResponse(
                Success=True,
                OutRefundNO=request.OutRefundNO,
                RefundID=data.get('refund_id', ''),
                RawResponse=data,
            )
        return RefundResponse(
            Success=False,
            OutRefundNO=request.OutRefundNO,
            RawResponse=data,
        )

    def Close(self, out_trade_no: str) -> bool:
        url = (
            f'{self.WECHAT_API_BASE}/v3/pay/transactions/out-trade-no/'
            f'{out_trade_no}/close'
        )
        body = json.dumps({'mchid': self._MchID}, separators=(',', ':'))
        resp = self._Request('POST', url, body)
        # 调用 _ParseResponse 统一检查验签标记（无 body 时返回 {}，验签失败时设 __success=False）
        data = self._ParseResponse(resp)
        return 200 <= resp.status_code < 300 and data.get('__success', True) is not False

    def Transfer(self, request: TransferRequest) -> TransferResponse:
        """商家转账到零钱——v3/transfer/batches"""
        if request.TransferType != 'wallet':
            return TransferResponse(
                Success=False,
                OutTransferNO=request.OutTransferNO,
                RawResponse={'error': '微信转账仅支持付款到零钱(wallet)'},
            )
        amount_cents = int((request.Amount * 100).to_integral_value())
        payload = {
            'appid': self._credentials.AppID,
            'out_batch_no': request.OutTransferNO,
            'batch_name': request.Description or '商家转账',
            'batch_remark': request.Description or '商家转账',
            'total_amount': amount_cents,
            'total_num': 1,
            'transfer_detail_list': [
                {
                    'out_detail_no': request.OutTransferNO,
                    'transfer_amount': amount_cents,
                    'transfer_remark': request.Description or '转账',
                    'openid': request.PayeeAccount,
                }
            ],
        }
        if request.Extra:
            payload.update(request.Extra)
        body = json.dumps(payload, separators=(',', ':'), ensure_ascii=False)
        url = f'{self.WECHAT_API_BASE}/v3/transfer/batches'
        resp = self._Request('POST', url, body)
        data = self._ParseResponse(resp)
        if resp.status_code in (200, 201) and (
            'batch_id' in data or 'out_batch_no' in data
        ) and data.get('__success', True) is not False:
            return TransferResponse(
                Success=True,
                OutTransferNO=request.OutTransferNO,
                TransferID=data.get('batch_id', data.get('out_batch_no', '')),
                RawResponse=data,
            )
        return TransferResponse(
            Success=False,
            OutTransferNO=request.OutTransferNO,
            RawResponse=data,
        )

    def QueryTransfer(self, out_transfer_no: str) -> TransferResponse:
        url = (
            f'{self.WECHAT_API_BASE}/v3/transfer/batches/out-batch-no/'
            f'{out_transfer_no}?need_query_detail=true'
        )
        resp = self._Request('GET', url, '')
        data = self._ParseResponse(resp)
        if resp.status_code == 200 and data.get('__success', True) is not False:
            return TransferResponse(
                Success=True,
                OutTransferNO=out_transfer_no,
                TransferID=data.get('batch_id', ''),
                RawResponse=data,
            )
        return TransferResponse(
            Success=False,
            OutTransferNO=out_transfer_no,
            RawResponse=data,
        )

    def QueryRefund(self, out_refund_no: str) -> RefundQueryResponse:
        """查询退款状态——GET /v3/refund/domestic/refunds/{out_refund_no}"""
        url = (
            f'{self.WECHAT_API_BASE}/v3/refund/domestic/refunds/'
            f'{out_refund_no}'
        )
        resp = self._Request('GET', url, '')
        data = self._ParseResponse(resp)
        if resp.status_code == 200 and data.get('__success', True) is not False:
            amount = data.get('amount', {})
            refund_amount_fen = amount.get('refund', 0)
            try:
                refund_amount = Decimal(str(refund_amount_fen)) / Decimal('100')
            except (InvalidOperation, ValueError, TypeError):
                refund_amount = Decimal('0')
            return RefundQueryResponse(
                Success=True,
                OutRefundNO=out_refund_no,
                RefundID=data.get('refund_id', ''),
                RefundStatus=data.get('status', ''),
                RefundAmount=refund_amount,
                RawResponse=data,
            )
        return RefundQueryResponse(
            Success=False,
            OutRefundNO=out_refund_no,
            RawResponse=data,
        )

    # ─── 回调处理 ─────────────────────────────────────────

    def VerifyNotify(self, raw_data: bytes, headers: Dict[str, str]) -> bool:
        """验证微信支付回调签名——使用平台证书 RSA-SHA256 验证 Wechatpay-Signature 头

        同时校验 Wechatpay-Timestamp 新鲜度（5 分钟窗口），防止重放攻击。
        """
        if not raw_data:
            return False
        signature = (
            headers.get('Wechatpay-Signature', '')
            or headers.get('wechatpay-signature', '')
        )
        timestamp = (
            headers.get('Wechatpay-Timestamp', '')
            or headers.get('wechatpay-timestamp', '')
        )
        nonce = (
            headers.get('Wechatpay-Nonce', '')
            or headers.get('wechatpay-nonce', '')
        )
        if not signature or not timestamp or not nonce:
            return False
        # 校验时间戳新鲜度（5 分钟窗口），防止重放攻击
        try:
            notify_ts = int(timestamp)
        except (ValueError, TypeError):
            return False
        if abs(int(time.time()) - notify_ts) > self._NOTIFY_TIME_WINDOW_SECONDS:
            logger.warning('[WechatPay] 回调时间戳超出 5 分钟窗口，疑似重放攻击')
            return False
        body = raw_data.decode('utf-8') if isinstance(raw_data, (bytes, bytearray)) else str(raw_data)
        return self._VerifySignature(timestamp, nonce, body, signature)

    def ParseNotify(self, raw_data: bytes) -> Dict[str, Any]:
        """解析微信支付回调——AES-GCM 解密 resource 后提取标准化字段"""
        try:
            body = (
                raw_data.decode('utf-8')
                if isinstance(raw_data, (bytes, bytearray))
                else str(raw_data)
            )
            envelope = json.loads(body)
            resource = envelope.get('resource', {})
            plaintext = self._DecryptAESCipher(resource)
            data = json.loads(plaintext) if plaintext else {}
            amount = data.get('amount', {})
            return {
                'out_trade_no': data.get('out_trade_no', ''),
                'out_refund_no': data.get('out_refund_no', ''),
                'transaction_id': data.get('transaction_id', ''),
                'trade_state': data.get('trade_state', ''),
                'trade_state_desc': data.get('trade_state_desc', ''),
                'total_amount': amount.get('total', 0),
                'payer_openid': data.get('payer', {}).get('openid', ''),
                'success_time': data.get('success_time', ''),
                'raw': data,
            }
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning(f'微信回调解析失败: {exc}')
            return {'out_trade_no': '', 'error': f'回调解析失败: {exc}'}

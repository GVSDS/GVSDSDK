"""MSYNC 认证模块

提供 HMAC-SHA256 签名验证、服务令牌编解码等纯函数。
Django ORM 依赖的函数（AuthenticateService, IssueServiceToken 等）
仍在 gvsds.apps.msync.auth 中，由服务端调用。
"""

import hmac
import hashlib
import time
import json
import base64
import binascii
import uuid
import logging
import datetime

logger = logging.getLogger(__name__)


def SignPayload(Payload, Secret):
    """使用 HMAC-SHA256 对载荷签名"""
    if isinstance(Payload, dict):
        Payload = json.dumps(Payload, sort_keys=True, separators=(',', ':'))
    if isinstance(Payload, str):
        Payload = Payload.encode('utf-8')
    return hmac.new(Secret.encode('utf-8'), Payload, hashlib.sha256).hexdigest()


def VerifySignature(Payload, Signature, Secret):
    """验证 HMAC-SHA256 签名"""
    Expected = SignPayload(Payload, Secret)
    return hmac.compare_digest(Expected, Signature)


def GenerateChallenge():
    """生成认证挑战字符串（时间戳 + 随机数）"""
    return f'{time.time():.6f}:{uuid.uuid4().hex}'


def BuildAuthPayload(ServiceName, Challenge, Timestamp=None):
    """构建认证请求载荷"""
    if Timestamp is None:
        Timestamp = int(time.time())
    return {
        'service': ServiceName,
        'challenge': Challenge,
        'ts': Timestamp,
    }


def SignAuthPayload(Payload, ServiceSecret):
    """对认证载荷签名"""
    Canonical = json.dumps(Payload, sort_keys=True, separators=(',', ':'))
    return SignPayload(Canonical, ServiceSecret)


def VerifyAuthPayload(Payload, Signature, ServiceSecret):
    """验证认证载荷签名"""
    Canonical = json.dumps(Payload, sort_keys=True, separators=(',', ':'))
    return VerifySignature(Canonical, Signature, ServiceSecret)


class ServiceTokenEncoder:
    """服务令牌编解码器——自包含令牌，无需数据库查询即可验证"""

    ALGORITHM = 'HS256'
    ISSUER = 'MSYNC'

    def __init__(self, SecretKey=None):
        if not SecretKey:
            raise ValueError('ServiceTokenEncoder 必须提供非空 SecretKey')
        self.SecretKey = SecretKey

    def Encode(self, service_name=None, scope=None, expires_at=None, master_secret=None, **kwargs):
        """编码令牌

        支持两种调用方式：
        1. 位置参数: Encode(payload_dict) — payload 包含所有字段
        2. 关键字参数: Encode(service_name=..., scope=..., expires_at=..., master_secret=...)
        """
        # 如果传入的是字典作为第一个位置参数
        if service_name is not None and isinstance(service_name, dict) and scope is None:
            Payload = service_name
            Secret = master_secret or self.SecretKey
        else:
            Secret = master_secret or self.SecretKey
            Payload = {
                'iss': self.ISSUER,
                'sub': service_name,
                'scope': scope or [],
            }
            if expires_at is not None:
                if hasattr(expires_at, 'timestamp'):
                    Payload['exp'] = int(expires_at.timestamp())
                else:
                    Payload['exp'] = int(expires_at)

        Header = json.dumps({'alg': self.ALGORITHM, 'typ': 'JWT'})
        PayloadData = json.dumps(Payload, sort_keys=True, separators=(',', ':'), default=str)
        HeaderB64 = base64.urlsafe_b64encode(Header.encode()).rstrip(b'=').decode()
        PayloadB64 = base64.urlsafe_b64encode(PayloadData.encode()).rstrip(b'=').decode()
        SigningInput = f'{HeaderB64}.{PayloadB64}'
        Signature = hmac.new(
            Secret.encode('utf-8'),
            SigningInput.encode('utf-8'),
            hashlib.sha256,
        ).hexdigest()
        return f'{SigningInput}.{Signature}'

    def Decode(self, Token, SecretKey=None):
        """解码并验证令牌（含 exp 过期校验）"""
        Secret = SecretKey or self.SecretKey
        if not Secret:
            logger.warning('ServiceTokenEncoder.Decode 缺少 SecretKey')
            return None
        try:
            Parts = Token.split('.')
            if len(Parts) != 3:
                return None
            HeaderB64, PayloadB64, Signature = Parts
            SigningInput = f'{HeaderB64}.{PayloadB64}'
            ExpectedSig = hmac.new(
                Secret.encode('utf-8'),
                SigningInput.encode('utf-8'),
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(ExpectedSig, Signature):
                return None
            Padding = 4 - len(PayloadB64) % 4
            if Padding != 4:
                PayloadB64 += '=' * Padding
            PayloadJSON = base64.urlsafe_b64decode(PayloadB64).decode('utf-8')
            Payload = json.loads(PayloadJSON)
            # 校验 exp 过期时间
            Exp = Payload.get('exp')
            if Exp is not None and int(time.time()) > int(Exp):
                logger.warning('ServiceTokenEncoder.Decode 令牌已过期')
                return None
            return Payload
        except (ValueError, TypeError, json.JSONDecodeError, binascii.Error):
            return None

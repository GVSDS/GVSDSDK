"""MSYNC 认证模块

提供 HMAC-SHA256 签名验证、服务令牌编解码等纯函数，
以及 ORM 依赖的服务端认证函数（AuthenticateService, IssueServiceToken 等，
模型延迟导入，主服务器侧使用）。
"""

import hmac
import hashlib
import time
import json
import base64
import binascii
import uuid
import logging
import traceback
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


# ─── ORM 依赖的认证函数（主服务器侧使用，模型延迟导入）──────────────

def _get_master_secret():
    """获取 MASTER_SECRET：环境变量 MSYNC_MASTER_SECRET 优先，其次 secrets.ini"""
    from gvsdsdk.secrets import get as _secret_get
    secret = _secret_get('msync', 'MASTER_SECRET', fallback=None)
    if not secret:
        from django.core.exceptions import ImproperlyConfigured
        raise ImproperlyConfigured('msync MASTER_SECRET 未配置，请在 secrets.ini 或环境变量 MSYNC_MASTER_SECRET 中设置')
    return secret


def IssueServiceToken(service_node, access_lifetime_hours=2, refresh_lifetime_days=30):
    """为服务节点签发令牌对（AccessToken + RefreshToken）"""
    from gvsdsdk.msync.models import ServiceToken

    master_secret = _get_master_secret()

    now_aware = datetime.datetime.now(datetime.timezone.utc)
    access_expires_aware = now_aware + datetime.timedelta(hours=access_lifetime_hours)
    refresh_expires_aware = now_aware + datetime.timedelta(days=refresh_lifetime_days)

    now_naive = now_aware.replace(tzinfo=None)
    access_expires = access_expires_aware.replace(tzinfo=None)
    refresh_expires = refresh_expires_aware.replace(tzinfo=None)

    service_scope = service_node.ServiceScope
    if service_scope is None:
        service_scope = ['ogm:read', 'ogm:write', 'registry:read']
    elif isinstance(service_scope, str):
        service_scope = [service_scope]

    encoder = ServiceTokenEncoder(master_secret)
    access_token = encoder.Encode(
        service_name=service_node.ServiceName,
        scope=service_scope,
        expires_at=access_expires_aware,
    )
    refresh_token = encoder.Encode(
        service_name=service_node.ServiceName,
        scope=service_scope,
        expires_at=refresh_expires_aware,
    )

    import uuid
    token = ServiceToken(
        TokenUUID=uuid.uuid4().bytes,
        ServiceUUID=service_node,
        AccessToken=access_token,
        RefreshToken=refresh_token,
        TokenScope=service_scope,
        ExpiresAt=access_expires,
        RefreshExpiresAt=refresh_expires,
    )
    token.save()
    return token


def AuthenticateService(service_name, signed_payload, signature):
    """验证服务身份并签发令牌"""
    from gvsdsdk.msync.models import ServiceNode, ServiceToken

    try:
        node = ServiceNode.objects.get(ServiceName=service_name, ServiceStatus=1)
    except ServiceNode.DoesNotExist:
        logger.error(f'[AUTH] 服务节点不存在或未激活: {service_name}')
        return None
    except Exception as e:
        logger.error(f'查询服务节点异常 [{service_name}]: {e}\n{traceback.format_exc()}')
        return None

    import time
    ts = signed_payload.get('ts', 0)
    now = time.time()
    ts_diff = abs(now - ts)
    if ts_diff > 300:
        logger.error(f'[AUTH] 时间戳校验失败: ts={ts}, now={now}, diff={ts_diff:.1f}s')
        return None

    raw_secret = node.GetSecret()
    if raw_secret is None:
        logger.error(f'服务 {service_name} 密钥加密副本缺失，请重新注册')
        return None

    if not VerifyAuthPayload(signed_payload, signature, raw_secret):
        logger.error(f'[AUTH] 签名验证失败: service={service_name}, payload={signed_payload}')
        return None

    try:
        ServiceToken.objects.filter(ServiceUUID=node, TokenStatus=1).update(TokenStatus=0)
    except Exception as e:
        logger.error(f'撤销旧令牌异常 [{service_name}]: {e}')

    try:
        return IssueServiceToken(node)
    except Exception as e:
        logger.error(f'签发服务令牌异常 [{service_name}]: {e}\n{traceback.format_exc()}')
        return None


def ValidateAccessToken(access_token):
    """验证 AccessToken 并返回载荷信息"""
    from gvsdsdk.msync.models import ServiceToken

    master_secret = _get_master_secret()
    encoder = ServiceTokenEncoder(master_secret)
    payload = encoder.Decode(access_token)
    if payload is None:
        return None

    try:
        token_record = ServiceToken.objects.get(AccessToken=access_token, TokenStatus=1)
        if token_record.IsExpired():
            token_record.Revoke()
            return None
    except ServiceToken.DoesNotExist:
        return None

    return payload


def RefreshServiceToken(refresh_token):
    """使用 RefreshToken 刷新令牌对"""
    from gvsdsdk.msync.models import ServiceToken, ServiceNode

    master_secret = _get_master_secret()
    encoder = ServiceTokenEncoder(master_secret)
    payload = encoder.Decode(refresh_token)
    if payload is None:
        return None

    try:
        old_token = ServiceToken.objects.get(RefreshToken=refresh_token, TokenStatus=1)
    except ServiceToken.DoesNotExist:
        return None

    if old_token.IsRefreshExpired():
        old_token.Revoke()
        return None

    service_name = payload.get('sub')
    try:
        node = ServiceNode.objects.get(ServiceName=service_name, ServiceStatus=1)
    except ServiceNode.DoesNotExist:
        return None

    old_token.Revoke()
    return IssueServiceToken(node)

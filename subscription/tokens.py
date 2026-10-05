"""订阅式软件提供——提供者子服务器访问令牌（自包含 HMAC-SHA256，无数据库依赖）

与 msync 的服务令牌同构（复用 ServiceTokenEncoder），差异在 payload：
    typ               = 'subscription_provider'（与 service/user 令牌区分）
    sub               = ApiKey（该软件那一对凭据的公开标识）
    SoftwareUUID      = 订阅组（产品）UUID —— 令牌被锁定到这一个软件
    ConsumerCompanies = 该软件的消费者公司 UUID 列表（供子服务器判断数据范围）

密钥只存在主服务器（bcrypt 哈希），令牌用 MASTER_SECRET 签名；
子服务器无需知道 MASTER_SECRET 也能使用令牌（只作 Bearer 透传）。
"""

import datetime

from gvsdsdk.msync.auth import ServiceTokenEncoder, _get_master_secret

#: 令牌类型标识（payload['typ']）
SUBSCRIPTION_TOKEN_TYPE = 'subscription_provider'

#: 访问令牌默认有效期（小时）
SUBSCRIPTION_TOKEN_TTL_HOURS = 2


def IssueSubscriptionToken(ApiKey, GroupUUID, ConsumerCompanyUUIDs=None,
                           TTLHours=SUBSCRIPTION_TOKEN_TTL_HOURS, MasterSecret=None):
    """签发订阅软件访问令牌

    Args:
        ApiKey: 软件接入凭据的 API Key
        GroupUUID: 订阅组（产品）UUID（str/bytes/UUIDObj）
        ConsumerCompanyUUIDs: 该软件的消费者公司 UUID 列表（可选，写入令牌便于子服务器判断）
        TTLHours: 有效期（小时）
        MasterSecret: MASTER_SECRET（缺省从配置读取）

    Returns:
        (Token, ExpiresAtTs) 或 (None, None)
    """
    from gvsdsdk.model_utils import UUIDObj

    if not ApiKey:
        return None, None
    try:
        group_str = str(UUIDObj(GroupUUID)) if GroupUUID else ''
    except Exception:
        group_str = str(GroupUUID) if GroupUUID else ''
    if not group_str:
        return None, None

    master = MasterSecret or _get_master_secret()
    now = datetime.datetime.now(datetime.timezone.utc)
    expires = now + datetime.timedelta(hours=TTLHours)
    companies = []
    for c in (ConsumerCompanyUUIDs or []):
        try:
            companies.append(str(UUIDObj(c)))
        except Exception:
            continue
    payload = {
        'iss': 'MSYNC',
        'typ': SUBSCRIPTION_TOKEN_TYPE,
        'sub': str(ApiKey),
        'SoftwareUUID': group_str,
        'ConsumerCompanies': companies,
        'exp': int(expires.timestamp()),
    }
    encoder = ServiceTokenEncoder(master)
    return encoder.Encode(payload, master_secret=master), int(expires.timestamp())


def IntrospectSubscriptionToken(Token, MasterSecret=None):
    """自省订阅软件令牌：校验签名/过期/类型

    Returns:
        dict：{'ApiKey', 'GroupUUID', 'ConsumerCompanies': [...]}；校验失败返回 None
    """
    if not Token:
        return None
    master = MasterSecret or _get_master_secret()
    encoder = ServiceTokenEncoder(master)
    payload = encoder.Decode(Token)
    if not payload or payload.get('typ') != SUBSCRIPTION_TOKEN_TYPE:
        return None
    api_key = payload.get('sub')
    group_uuid = payload.get('SoftwareUUID')
    if not api_key or not group_uuid:
        return None
    return {
        'ApiKey': api_key,
        'GroupUUID': group_uuid,
        'ConsumerCompanies': payload.get('ConsumerCompanies') or [],
    }

"""gvsdsdk.subscription —— 订阅式软件提供方 SDK

提供者子服务器侧用它把「软件的 API Key + 密钥」换成访问令牌，并携带令牌调用主服务器接口：

    from gvsdsdk.subscription import SubscriptionClient

    client = SubscriptionClient(MasterURL='https://gerp.gvsds.com',
                                ApiKey='sk_xxx', Secret='一次性展示的密钥')
    client.FetchToken()          # 换取访问令牌（内存缓存，过期自动重取）
    client.Request('GET', '/API/Subscription/Provider/Ping')

服务端侧用 tokens 模块签发与自省令牌（纯 HMAC，无数据库依赖）。
"""

from gvsdsdk.subscription.client import SubscriptionClient, SubscriptionClientError
from gvsdsdk.subscription.tokens import (
    SUBSCRIPTION_TOKEN_TTL_HOURS,
    SUBSCRIPTION_TOKEN_TYPE,
    IntrospectSubscriptionToken,
    IssueSubscriptionToken,
)

__all__ = [
    'SubscriptionClient',
    'SubscriptionClientError',
    'IssueSubscriptionToken',
    'IntrospectSubscriptionToken',
    'SUBSCRIPTION_TOKEN_TYPE',
    'SUBSCRIPTION_TOKEN_TTL_HOURS',
]

"""订阅式软件提供——提供者子服务器客户端

用「软件（产品）的 API Key + 密钥」向主服务器换取访问令牌，并携带令牌调用订阅接口。
仅依赖标准库（urllib），便于子服务器单独引用；不依赖 Django ORM。

用法：
    client = SubscriptionClient(MasterURL='https://gerp.gvsds.com',
                                ApiKey='sk_xxx', Secret='密钥明文（仅激活/重置时展示一次）')
    token = client.FetchToken()['AccessToken']
    data = client.Request('GET', '/API/Subscription/Provider/Ping')
"""

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

#: 令牌到期前的提前续期余量（秒）
TOKEN_REFRESH_SKEW_SECONDS = 60

#: 默认请求超时（秒）
DEFAULT_TIMEOUT = 10


def _ResolveMasterURL(MasterURL=None):
    """解析主服务器地址（复用 msync 的解析链：参数 → settings → 环境变量 → 默认值）"""
    from gvsdsdk.msync.client import _ResolveMasterURL as _MsyncResolveMasterURL
    return _MsyncResolveMasterURL(MasterURL)


class SubscriptionClientError(Exception):
    """订阅客户端错误（携带 HTTP 状态码与响应体，便于上层定位）"""

    def __init__(self, Message, StatusCode=None, ResponseBody=None):
        super().__init__(Message)
        self.StatusCode = StatusCode
        self.ResponseBody = ResponseBody


class SubscriptionClient:
    """提供者子服务器侧客户端

    Args:
        ApiKey: 软件的 API Key
        Secret: 软件的密钥（明文，仅在激活/重置时展示一次）
        MasterURL: 主服务器地址（缺省走 msync 的解析链）
        Timeout: 请求超时秒数
    """

    def __init__(self, ApiKey, Secret, MasterURL=None, Timeout=DEFAULT_TIMEOUT):
        if not ApiKey or not Secret:
            raise ValueError('SubscriptionClient 必须提供 ApiKey 与 Secret')
        self.ApiKey = ApiKey
        self.Secret = Secret
        self.MasterURL = _ResolveMasterURL(MasterURL)
        self.Timeout = Timeout
        self._AccessToken = None
        self._ExpiresAt = 0.0

    @property
    def AccessToken(self):
        return self._AccessToken

    @property
    def IsTokenValid(self):
        return bool(self._AccessToken) and time.time() < (self._ExpiresAt - TOKEN_REFRESH_SKEW_SECONDS)

    def FetchToken(self):
        """用 ApiKey + 密钥换取访问令牌（成功后写入内存缓存）

        Returns:
            dict：{'AccessToken', 'ExpiresAt', 'SoftwareUUID', 'GroupName'}
        """
        payload = self._HttpRequest(
            'POST', '/API/Subscription/Provider/Token',
            Json={'ApiKey': self.ApiKey, 'Secret': self.Secret},
        )
        data = payload.get('data') or {}
        token = data.get('AccessToken')
        if not token:
            raise SubscriptionClientError('换取令牌失败：响应缺少 AccessToken', ResponseBody=payload)
        self._AccessToken = token
        self._ExpiresAt = float(data.get('ExpiresAt') or 0) or (time.time() + 3600)
        return data

    def EnsureToken(self):
        """确保有可用令牌（无效则自动换取）"""
        if not self.IsTokenValid:
            self.FetchToken()
        return self._AccessToken

    def Request(self, Method, Path, Json=None, Params=None):
        """携带访问令牌调用主服务器接口

        Args:
            Method: HTTP 方法（GET/POST/...）
            Path: 接口路径（如 /API/Subscription/Provider/Ping）
            Json: 请求体（POST 用）
            Params: 查询参数（dict）

        Returns:
            dict：主服务器统一响应体 {code, msg, data}
        """
        token = self.EnsureToken()
        headers = {'Authorization': f'Bearer {token}'}
        return self._HttpRequest(Method, Path, Json=Json, Params=Params, Headers=headers)

    # ── 内部：HTTP 收发 ────────────────────────────────────────────

    def _HttpRequest(self, Method, Path, Json=None, Params=None, Headers=None):
        url = f'{self.MasterURL}{Path}'
        if Params:
            url = f'{url}?{urllib.parse.urlencode(Params)}'
        body = None
        headers = dict(Headers or {})
        if Json is not None:
            body = json.dumps(Json).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(url, data=body, headers=headers, method=Method.upper())
        try:
            with urllib.request.urlopen(request, timeout=self.Timeout) as response:
                raw = response.read().decode('utf-8') or '{}'
        except urllib.error.HTTPError as e:
            raw = e.read().decode('utf-8', errors='replace')
            raise SubscriptionClientError(
                f'主服务器返回 {e.code}', StatusCode=e.code, ResponseBody=raw,
            ) from e
        except urllib.error.URLError as e:
            raise SubscriptionClientError(f'无法连接主服务器：{e.reason}') from e
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise SubscriptionClientError('主服务器响应不是合法 JSON', ResponseBody=raw) from e

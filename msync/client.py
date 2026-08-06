"""MSYNC 服务客户端模块

提供 ServiceClient 类，用于微服务间发起认证请求。
支持自动令牌获取与刷新、OGM 解析、服务发现。
本模块自包含实现（使用 urllib），不依赖 Django。
"""

import json
import time
import uuid
import logging
import urllib.request
import urllib.error
from urllib.parse import quote

logger = logging.getLogger(__name__)


MSYNC_AUTH_PATH = '/api/msync/auth/token/'
MSYNC_AUTH_REFRESH_PATH = '/api/msync/auth/token/refresh/'
MSYNC_OGM_RESOLVE_PATH = '/api/msync/ogm/{global_id}/'
MSYNC_REGISTRY_PATH = '/api/msync/registry/{name}/'

#: 默认 MasterURL（仅当 settings 与环境变量均未配置时使用）
DEFAULT_MASTER_URL = 'https://api.gvsds.com'

from gvsdsdk.msync.auth import SignPayload, BuildAuthPayload


def _ResolveMasterURL(master_url):
    """解析 MasterURL：优先参数 → Django settings → 环境变量 → 默认值

    本模块自包含（不依赖 Django ORM/HTTP），但允许通过 settings 或环境变量覆盖默认地址。
    """
    if master_url:
        return master_url.rstrip('/')
    # 尝试 Django settings（软依赖，非 Django 环境下跳过）
    try:
        from django.conf import settings as _settings
        url = getattr(_settings, 'GVSDSDK_MSYNC_MASTER_URL', None)
        if url:
            return str(url).rstrip('/')
    except Exception:
        pass
    # 回退到环境变量
    import os as _os
    url = _os.environ.get('GVSDSDK_MSYNC_MASTER_URL')
    if url:
        return url.rstrip('/')
    return DEFAULT_MASTER_URL


class ServiceClientError(Exception):
    def __init__(self, Message, StatusCode=None, ResponseBody=None):
        super().__init__(Message)
        self.StatusCode = StatusCode
        self.ResponseBody = ResponseBody


class _TokenStore:
    """内存中的令牌存储"""

    def __init__(self):
        self._AccessToken = None
        self._RefreshToken = None
        self._ExpiresAt = 0.0
        self._Scope = []

    @property
    def AccessToken(self):
        return self._AccessToken

    @property
    def RefreshToken(self):
        return self._RefreshToken

    @property
    def Scope(self):
        return self._Scope

    def IsValid(self):
        return self._AccessToken is not None and time.time() < self._ExpiresAt

    def Store(self, AccessToken, RefreshToken, ExpiresAt, Scope=None):
        self._AccessToken = AccessToken
        self._RefreshToken = RefreshToken
        self._ExpiresAt = ExpiresAt
        self._Scope = Scope or []

    def Clear(self):
        self._AccessToken = None
        self._RefreshToken = None
        self._ExpiresAt = 0.0
        self._Scope = []


class ServiceClient:
    """微服务通信客户端——用于向其他服务发起认证请求

    用法:
        client = ServiceClient(
            ServiceName='gerp',
            ServiceSecret='your-secret',
        )
        client.Authenticate()

        # 获取其他服务信息
        info = client.DiscoverService('crb')

        # 解析 OGM
        ogm = client.ResolveOGM('user:abc123')

        # 向其他服务发请求
        response = client.Request('GET', 'https://crb.gvsds.com/api/wallet', Scope=['ledger:read'])
    """

    def __init__(self, ServiceName, ServiceSecret, MasterURL=None):
        self.ServiceName = ServiceName
        self.ServiceSecret = ServiceSecret
        self.MasterURL = _ResolveMasterURL(MasterURL)
        self._tokens = _TokenStore()

    @property
    def IsAuthenticated(self):
        return self._tokens.IsValid()

    @property
    def AccessToken(self):
        return self._tokens.AccessToken

    @property
    def RefreshToken(self):
        return self._tokens.RefreshToken

    def Authenticate(self):
        if not self._Authenticate():
            raise ServiceClientError('认证失败')
        return self

    def Refresh(self):
        if not self._RefreshToken():
            raise ServiceClientError('刷新失败')
        return self

    def _Authenticate(self):
        challenge = _GenerateChallenge()
        payload = BuildAuthPayload(self.ServiceName, challenge)
        signature = SignPayload(payload, self.ServiceSecret)

        req_body = json.dumps({
            'ServiceName': self.ServiceName,
            'Payload': payload,
            'Signature': signature,
        }).encode('utf-8')

        req = urllib.request.Request(
            f'{self.MasterURL}{MSYNC_AUTH_PATH}',
            data=req_body,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', errors='replace')
            logger.error(f'认证失败 HTTP {e.code}: {body[:200]}')  # 限制日志长度，避免泄漏完整 token
            return False
        except (urllib.error.URLError, ValueError, OSError) as e:
            logger.error(f'认证异常: {e}')
            return False

        if data.get('code') not in (200, 0):
            logger.error(f'认证业务错误 code={data.get("code")} msg={data.get("msg", "")[:200]}')  # 仅记录 code 和简短消息
            return False

        token_data = data.get('data') or {}
        self._tokens.Store(
            AccessToken=token_data.get('AccessToken'),
            RefreshToken=token_data.get('RefreshToken'),
            ExpiresAt=token_data.get('ExpiresAt', 0),
            Scope=token_data.get('Scope', []),
        )
        return self._tokens.AccessToken is not None

    def _RefreshToken(self):
        if not self._tokens.RefreshToken:
            return False

        req_body = json.dumps({
            'ServiceName': self.ServiceName,
            'RefreshToken': self._tokens.RefreshToken,
        }).encode('utf-8')

        req = urllib.request.Request(
            f'{self.MasterURL}{MSYNC_AUTH_REFRESH_PATH}',
            data=req_body,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode('utf-8'))
        except (urllib.error.URLError, ValueError, OSError) as e:
            logger.error(f'刷新令牌异常: {e}')
            return False

        if data.get('code') not in (200, 0):
            return False

        token_data = data.get('data') or {}
        self._tokens.Store(
            AccessToken=token_data.get('AccessToken'),
            RefreshToken=token_data.get('RefreshToken'),
            ExpiresAt=token_data.get('ExpiresAt', 0),
            Scope=token_data.get('Scope', []),
        )
        return self._tokens.AccessToken is not None

    def _EnsureAuthenticated(self):
        if self.IsAuthenticated:
            return
        if self._tokens.RefreshToken:
            self._RefreshToken()
            return
        self._Authenticate()

    # 注：本方法约 60 行且含 3 层 try/except 嵌套（M9）。
    # 未拆分原因：401 重试逻辑与首次请求共享 req_headers/body/URL/Method/Timeout 等局部状态，
    # 拆分为独立方法需传递大量参数或改用闭包，收益有限且可能影响现有测试覆盖，故保持原结构。
    def Request(self, Method, URL, Data=None, Headers=None, Scope=None, Timeout=30):
        self._EnsureAuthenticated()

        req_headers = {
            'Authorization': f'Service {self.AccessToken}',
            'Content-Type': 'application/json',
            'X-Source-Service': self.ServiceName,
        }

        if Scope:
            req_headers['X-Required-Scope'] = ','.join(Scope)

        if Headers:
            req_headers.update(Headers)

        body = None
        if Data is not None:
            if isinstance(Data, dict):
                body = json.dumps(Data).encode('utf-8')
            elif isinstance(Data, bytes):
                body = Data
            elif isinstance(Data, str):
                body = Data.encode('utf-8')

        req = urllib.request.Request(URL, data=body, headers=req_headers, method=Method)

        try:
            with urllib.request.urlopen(req, timeout=Timeout) as resp:
                response_data = resp.read().decode('utf-8')
                content_type = resp.headers.get('Content-Type', '')
                if 'application/json' in content_type:
                    return json.loads(response_data)
                return response_data
        except urllib.error.HTTPError as e:
            body_text = e.read().decode('utf-8', errors='replace')
            if e.code == 401:
                logger.info('令牌可能已失效，尝试刷新')
                self._tokens.Clear()
                self._EnsureAuthenticated()
                if not self.AccessToken:
                    raise ServiceClientError('重新认证失败，无法重试请求')
                req_headers['Authorization'] = f'Service {self.AccessToken}'
                retry_req = urllib.request.Request(URL, data=body, headers=req_headers, method=Method)
                try:
                    with urllib.request.urlopen(retry_req, timeout=Timeout) as resp2:
                        response_data = resp2.read().decode('utf-8')
                        content_type = resp2.headers.get('Content-Type', '')
                        if 'application/json' in content_type:
                            return json.loads(response_data)
                        return response_data
                except urllib.error.HTTPError as e2:
                    body2 = e2.read().decode('utf-8', errors='replace')
                    raise ServiceClientError(
                        f'重试后仍失败: HTTP {e2.code}',
                        StatusCode=e2.code,
                        ResponseBody=body2,
                    )
            raise ServiceClientError(
                f'请求失败: HTTP {e.code}',
                StatusCode=e.code,
                ResponseBody=body_text,
            )

    def ResolveOGM(self, GlobalID):
        url = f'{self.MasterURL}{MSYNC_OGM_RESOLVE_PATH.format(global_id=quote(str(GlobalID), safe=""))}'
        return self.Request('GET', url, Scope=['ogm:read'])

    def DiscoverService(self, ServiceName):
        url = f'{self.MasterURL}{MSYNC_REGISTRY_PATH.format(name=quote(str(ServiceName), safe=""))}'
        return self.Request('GET', url, Scope=['registry:read'])

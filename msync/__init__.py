"""GVSDS SDK MSYNC 子包

MSYNC 全部实现位于 gvsdsdk.msync（原生实现，非封装层）：
  - models: ServiceNode / ServiceToken / OGM / 分布式事务模型
  - auth: HMAC-SHA256 签名验证、服务令牌编解码、服务端签发/验证
  - registry / ogm / transaction: 服务注册发现 / OGM 管理 / Saga 编排
  - client: 微服务通信客户端（REST）
  - local_tx: 本地事务步骤处理器
  - middleware / views / serializers / urls / sio / admin: Django 集成层
"""

default_app_config = 'gvsdsdk.msync.apps.MsyncConfig'

# 自有实现（无 Django ORM 依赖，子服务器可直接使用）
from gvsdsdk.msync.auth import (  # noqa
    SignPayload,
    VerifySignature,
    GenerateChallenge,
    BuildAuthPayload,
    SignAuthPayload,
    VerifyAuthPayload,
    ServiceTokenEncoder,
)
from gvsdsdk.msync.client import ServiceClient, ServiceClientError  # noqa
from gvsdsdk.msync.local_tx import LocalTransactionManager, StepHandler, local_tx  # noqa

# ORM 依赖的模块延迟导入（仅在主服务器使用）
def __getattr__(name):
    _server_exports = {
        'ServiceRegistry': 'gvsdsdk.msync.registry',
        'RegistryError': 'gvsdsdk.msync.registry',
        'OGMManager': 'gvsdsdk.msync.ogm',
        'OGMError': 'gvsdsdk.msync.ogm',
        'TransactionManager': 'gvsdsdk.msync.transaction',
        'TransactionError': 'gvsdsdk.msync.transaction',
        'StepExecutionError': 'gvsdsdk.msync.transaction',
        'AuthenticateService': 'gvsdsdk.msync.auth',
        'RefreshServiceToken': 'gvsdsdk.msync.auth',
        'ValidateAccessToken': 'gvsdsdk.msync.auth',
        'IssueServiceToken': 'gvsdsdk.msync.auth',
    }
    if name in _server_exports:
        import importlib
        mod = importlib.import_module(_server_exports[name])
        return getattr(mod, name)
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


__all__ = [
    # auth (自有)
    'SignPayload',
    'VerifySignature',
    'GenerateChallenge',
    'BuildAuthPayload',
    'SignAuthPayload',
    'VerifyAuthPayload',
    'ServiceTokenEncoder',
    # auth (服务端)
    'AuthenticateService',
    'RefreshServiceToken',
    'ValidateAccessToken',
    'IssueServiceToken',
    # client (自有)
    'ServiceClient',
    'ServiceClientError',
    # local_tx (自有)
    'LocalTransactionManager',
    'StepHandler',
    'local_tx',
    # registry (服务端)
    'ServiceRegistry',
    'RegistryError',
    # ogm (服务端)
    'OGMManager',
    'OGMError',
    # transaction (服务端)
    'TransactionManager',
    'TransactionError',
    'StepExecutionError',
]

"""GVSDS SDK MSYNC 子包

将 MSYNC 相关模块统一组织在 gvsdsdk.msync 命名空间下。

自有实现（无 Django 依赖，子服务器可直接使用）：
  - auth: HMAC-SHA256 签名验证、服务令牌编解码
  - client: 微服务通信客户端（REST）
  - local_tx: 本地事务步骤处理器

服务端转发（依赖 Django ORM，仅在主服务器可用）：
  - registry: 服务注册/发现
  - ogm: 对象全局映射
  - transaction: Saga 分布式事务
"""

# 自有实现（无 Django 依赖，始终可用）
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

# 服务端转发（Django ORM 依赖，延迟导入避免子服务器初始化报错）
def __getattr__(name):
    _server_exports = {
        'ServiceRegistry': 'gvsdsdk.msync.registry',
        'RegistryError': 'gvsdsdk.msync.registry',
        'OGMManager': 'gvsdsdk.msync.ogm',
        'OGMError': 'gvsdsdk.msync.ogm',
        'TransactionManager': 'gvsdsdk.msync.transaction',
        'TransactionError': 'gvsdsdk.msync.transaction',
        'StepExecutionError': 'gvsdsdk.msync.transaction',
        'AuthenticateService': 'gvsds.apps.msync.auth',
        'RefreshServiceToken': 'gvsds.apps.msync.auth',
        'ValidateAccessToken': 'gvsds.apps.msync.auth',
        'IssueServiceToken': 'gvsds.apps.msync.auth',
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
    # auth (服务端转发)
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
    # registry (服务端转发)
    'ServiceRegistry',
    'RegistryError',
    # ogm (服务端转发)
    'OGMManager',
    'OGMError',
    # transaction (服务端转发)
    'TransactionManager',
    'TransactionError',
    'StepExecutionError',
]

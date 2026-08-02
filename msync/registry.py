"""MSYNC 注册中心模块

薄封装：直接转发到 gvsds.apps.msync.registry 原生层。
"""

try:
    from gvsds.apps.msync.registry import ServiceRegistry, RegistryError
except ImportError:
    raise ImportError(
        'ServiceRegistry 仅在主服务器环境可用。'
        '子服务器请通过 ServiceClient.DiscoverService() 使用 HTTP 转发。'
    )

__all__ = [
    'ServiceRegistry',
    'RegistryError',
]

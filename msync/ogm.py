"""MSYNC OGM（对象全局映射）模块

薄封装：直接转发到 gvsds.apps.msync.ogm 原生层。
"""

try:
    from gvsds.apps.msync.ogm import OGMManager, OGMError
except ImportError:
    raise ImportError(
        'OGMManager 仅在主服务器环境可用。'
        '子服务器请通过 ServiceClient.ResolveOGM() 使用 HTTP 转发。'
    )

__all__ = [
    'OGMManager',
    'OGMError',
]

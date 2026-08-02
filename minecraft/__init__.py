"""gvsdsdk.minecraft — Minecraft 服务器插件扩展模块

提供 MC 插件共享表的 ORM 映射与认证服务：

- ``EasyAuth``：EasyAuth 插件认证表（managed=False，外部 schema）
- ``EasyAuthManager``：游戏账号密码验证、查询、绑定标记管理

注：本模块的模型 ``app_label='mc'``，在主服务器（gvsds）侧通过
``gvsds/db_router.py`` 路由到 minecraft 库；子服务器一般不需要访问。
"""

from .models import EasyAuth
from .auth import EasyAuthManager

__all__ = [
    'EasyAuth',
    'EasyAuthManager',
]

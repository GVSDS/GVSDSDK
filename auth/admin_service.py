"""gvsdsdk.auth.admin_service — 管理员身份统一判定服务

提供管理员身份判定的统一入口，基于两个层级的管理员权限码：

    ``admin:super``   —— 跨租户管理员（如 GERP 测试员），关闭租户隔离 + 公司隔离
    ``admin:tenant``  —— 租户内管理员（如 集团超级管理员），仅关闭公司隔离，保留租户隔离

判定顺序（适用于所有判定函数）：
    1. 用户未认证 → False
    2. 有当前角色 → 仅依据该角色的权限码判定（角色超级管理员模式）
       超级用户切换到普通角色时也应用隔离，不再因 IsSuperuser/IsStaff 直接放行
    3. 无当前角色 → IsSuperuser 或 IsStaff 作为系统维护回退（仅授予 admin:tenant 级别，
       不授予 admin:super，防止超级用户在未切换角色时获得跨租户越权能力）
    4. 否则 → False

提供的函数：
    ``IsAdmin``      —— 是否为管理员（admin:super 或 admin:tenant），用于权限校验
    ``IsSuperAdmin`` —— 是否为跨租户管理员（仅 admin:super），用于租户隔离判定
    ``IsTenantAdmin``—— 是否为租户内管理员（仅 admin:tenant），用于租户内管理员判定

用法：
    from gvsdsdk.auth.admin_service import IsAdmin, IsSuperAdmin

    if IsAdmin(request):
        ...  # 权限校验放行

    if IsSuperAdmin(request):
        ...  # 关闭租户隔离
"""

from __future__ import annotations

import logging as _logging

_logger = _logging.getLogger(__name__)


def _IsAuthenticated(user) -> bool:
    """兼容 gvsdsdk User（IsAuthenticated 属性）与 Django User（is_authenticated 属性）"""
    if user is None:
        return False
    for attr in ('IsAuthenticated', 'is_authenticated'):
        val = getattr(user, attr, None)
        if val:
            return bool(val)
    return False


def _HasPerm(request=None, user=None, role_uuid=None, perm_code=None) -> bool:
    """判断当前角色是否拥有指定权限码

    内部函数，供 IsAdmin / IsSuperAdmin / IsTenantAdmin 复用。
    判定顺序：
        1. 用户未认证 → False
        2. 有当前角色 → 仅依据该角色的指定权限码判定（角色超级管理员模式）
        3. 无当前角色 → IsSuperuser 或 IsStaff 作为系统维护回退（仅授予 admin:tenant 级别，
           perm_code='admin:super' 时直接返回 False，防止跨租户越权）
        4. 否则 → False
    """
    if perm_code is None:
        return False

    # 1. 解析 user
    if request is not None and user is None:
        user = getattr(request, 'user', None)
    if not _IsAuthenticated(user):
        return False

    # 2. 解析 role_uuid（优先从参数，其次从 request.current_role）
    if role_uuid is None and request is not None:
        current_role = getattr(request, 'current_role', None)
        if current_role:
            role_uuid = getattr(current_role, 'RoleUUID', None)

    # 3. 有当前角色时，仅依据该角色的指定权限码判定（角色超级管理员模式）
    if role_uuid:
        try:
            from gvsdsdk.models import RolePermission, Permission
            perm_uuids = Permission.objects.filter(
                PermCode=perm_code,
                PermStatus=1,
            ).values_list('PermUUID', flat=True)
            return RolePermission.objects.filter(
                RoleUUID=role_uuid,
                PermUUID__in=perm_uuids,
            ).exists()
        except Exception:
            _logger.warning('查询权限 %s 失败', perm_code, exc_info=True)
            return False

    # 4. 无当前角色时，IsSuperuser/IsStaff 作为系统维护回退（无角色无法做权限判定）
    # 安全策略：无角色时仅授予 admin:tenant 级别权限（不授予 admin:super），
    # 防止超级用户在未切换角色时获得跨租户越权能力。
    # superuser 无角色 → IsAdmin=True / IsTenantAdmin=True / IsSuperAdmin=False
    if perm_code == 'admin:super':
        return False
    return bool(getattr(user, 'IsSuperuser', False) or getattr(user, 'IsStaff', False))


def IsAdmin(request=None, user=None, role_uuid=None) -> bool:
    """判断用户是否为管理员（admin:super 或 admin:tenant）

    用于权限校验：两种层级的管理员都应放行。
    跨租户管理员（admin:super）和租户内管理员（admin:tenant）都是管理员。

    参数：
        request: 请求对象。优先从 ``request.user`` 取用户、``request.current_role`` 取角色。
        user: 用户对象。``request`` 为空时使用此参数。
        role_uuid: 角色 UUID（bytes 或 UUIDObj）。``request`` 为空时使用此参数；
                   若 ``request`` 非空则优先使用 ``request.current_role.RoleUUID``。

    返回：
        bool
    """
    if _HasPerm(request, user, role_uuid, 'admin:super'):
        return True
    return _HasPerm(request, user, role_uuid, 'admin:tenant')


def IsSuperAdmin(request=None, user=None, role_uuid=None) -> bool:
    """判断用户是否为跨租户管理员（仅 admin:super）

    用于隔离控制：仅 admin:super 关闭租户隔离，可跨租户查看数据。
    """
    return _HasPerm(request, user, role_uuid, 'admin:super')


def IsTenantAdmin(request=None, user=None, role_uuid=None) -> bool:
    """判断用户是否为租户内管理员（仅 admin:tenant）

    用于租户内管理员判定：仅关闭公司隔离，保留租户隔离。
    """
    return _HasPerm(request, user, role_uuid, 'admin:tenant')


def IsCompanyAdmin(request=None, user=None, role_uuid=None) -> bool:
    """判断用户是否为公司管理员（admin:company / admin:tenant / admin:super）

    公司管理员权限键 ``admin:company`` 由管理员在角色权限分配时手动勾选，
    绑定到具体公司（RolePermission.CompanyUUID 维度）后，仅在该公司内生效。

    继承规则（公司管理员能力）：
        - admin:super  —— 跨租户管理员，天然具备
        - admin:tenant —— 租户内管理员，自动继承（可管理租户下所有公司）
        - admin:company—— 手动授予的角色，需按公司维度绑定

    返回：
        bool
    """
    if _HasPerm(request, user, role_uuid, 'admin:company'):
        return True
    # 租户管理员 / 跨租户管理员自动继承公司管理员权限
    if IsAdmin(request=request, user=user, role_uuid=role_uuid):
        return True
    return False

from gvsdsdk.models import UserRole, Role, RolePermission, Permission, UserABAC
from gvsdsdk.model_utils import UUIDObj, _GetUserModel
from rest_framework.permissions import BasePermission
from django.db import DatabaseError

import logging as _logging
_logger = _logging.getLogger(__name__)


def IsAdminScopeDisabled(request):
    """判断是否为管理员特权模式（当前角色拥有 admin:super 或 admin:tenant 权限），此时关闭公司隔离

    与 FormService 行为一致：管理员自动关闭公司隔离，无需前端传 admin_verify=1。
    管理员判定委托给 ``gvsdsdk.auth.admin_service.IsAdmin``，统一权限查询逻辑。
    """
    if not request:
        return False
    from gvsdsdk.auth.admin_service import IsAdmin
    return IsAdmin(request)


def IsTenantScopeDisabled(request):
    """判断是否为跨租户管理员特权模式（当前角色拥有 admin:super 权限），此时关闭租户隔离

    仅 admin:super（跨租户管理员）可关闭租户隔离，admin:tenant（租户内管理员）保留租户隔离。
    管理员判定委托给 ``gvsdsdk.auth.admin_service.IsSuperAdmin``。
    """
    if not request:
        return False
    from gvsdsdk.auth.admin_service import IsSuperAdmin
    return IsSuperAdmin(request)


class IsAdminUser(BasePermission):
    """仅允许管理员（当前角色拥有 admin:super 或 admin:tenant 权限）访问

    两级管理员体系：
    - admin:super：跨租户管理员（GERP 测试员），关闭租户隔离 + 公司隔离
    - admin:tenant：租户内管理员（集团超级管理员），仅关闭公司隔离，保留租户隔离

    依据当前角色的权限判定，超级用户切换到普通角色时不再自动放行，
    需切换到拥有 admin:super 或 admin:tenant 权限的角色。
    无当前角色时回退到 IsSuperuser/IsStaff（系统维护场景）。
    """

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if not isinstance(user, _GetUserModel()):
            return False
        from gvsdsdk.auth.admin_service import IsAdmin
        return IsAdmin(request)


class IsCompanyAdminUser(BasePermission):
    """仅允许公司管理员及以上访问（admin:company / admin:tenant / admin:super）

    三级管理员体系（由窄到宽）：
    - ``admin:company``：公司管理员，按 RolePermission.CompanyUUID 绑定到具体公司后生效
    - ``admin:tenant`` ：租户内管理员，自动继承公司管理员能力
    - ``admin:super``  ：跨租户管理员，天然具备

    判定委托给 ``gvsdsdk.auth.admin_service.IsCompanyAdmin``。本类只负责"能不能进"，
    数据范围收窄（本公司/本租户/全部）由视图按 IsSuperAdmin/IsTenantAdmin 另行计算。
    """

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if not isinstance(user, _GetUserModel()):
            return False
        from gvsdsdk.auth.admin_service import IsCompanyAdmin
        return IsCompanyAdmin(request)


class IsCompanyMember(BasePermission):
    """验证用户当前角色属于目标数据所在公司

    安全策略：
    - has_permission：必须存在 current_company_uuid，否则拒绝（防止无公司上下文越权）
    - has_object_permission：
        * 对象无 CompanyUUID 字段（全局数据如 Tenant/Role）→ 放行
        * 对象有 CompanyUUID 字段但值为空（数据异常）→ 拒绝
        * 否则按公司 UUID 严格匹配
    """

    def has_permission(self, request, view):
        return bool(getattr(request, 'current_company_uuid', None))

    def has_object_permission(self, request, view, obj):
        if not request.current_company_uuid:
            return False
        # 区分"无字段"与"字段为空"：前者为全局数据，后者为异常数据
        if not hasattr(obj, 'CompanyUUID'):
            return True  # 无公司字段的全局数据不做限制
        obj_company = obj.CompanyUUID
        if obj_company is None:
            return False  # 有字段但值为空，疑似数据异常，默认拒绝
        return UUIDObj(obj_company) == UUIDObj(request.current_company_uuid)


class CompanyScopedMixin:
    """ViewSet Mixin：自动按当前角色所属公司过滤 queryset"""
    company_field = 'CompanyUUID'  # 可在 ViewSet 中覆盖

    def _is_admin_scope_disabled(self):
        """判断是否为管理员特权模式（委托给模块级 IsAdminScopeDisabled）"""
        return IsAdminScopeDisabled(getattr(self, 'request', None))

    def get_queryset(self):
        qs = super().get_queryset()
        # 管理员特权模式关闭隔离
        if self._is_admin_scope_disabled():
            return qs
        company_uuid = getattr(self.request, 'current_company_uuid', None)
        if company_uuid and hasattr(qs.model, self.company_field):
            qs = qs.filter(**{self.company_field: company_uuid})
        return qs

    def perform_create(self, serializer):
        company_uuid = getattr(self.request, 'current_company_uuid', None)
        if company_uuid and hasattr(serializer.Meta.model, 'CompanyUUID'):
            serializer.save(CompanyUUID=company_uuid)
        else:
            serializer.save()


class TenantScopedMixin:
    """ViewSet Mixin：自动按当前角色所属租户过滤 queryset

    用于纯租户级表（如 Tenant/Role/CompanyInfo），这些表无 CompanyUUID 字段，
    只能按 TenantUUID 过滤。仅跨租户管理员（admin:super）关闭租户隔离，
    租户内管理员（admin:tenant）保留租户隔离。
    """
    tenant_field = 'TenantUUID'  # 可在 ViewSet 中覆盖

    def _is_admin_scope_disabled(self):
        """判断是否为跨租户管理员特权模式（委托给模块级 IsTenantScopeDisabled）

        仅 admin:super 关闭租户隔离，admin:tenant 保留租户隔离。
        """
        return IsTenantScopeDisabled(getattr(self, 'request', None))

    def get_queryset(self):
        qs = super().get_queryset()
        if self._is_admin_scope_disabled():
            return qs
        tenant_uuid = getattr(self.request, 'current_tenant_uuid', None)
        if tenant_uuid and hasattr(qs.model, self.tenant_field):
            qs = qs.filter(**{self.tenant_field: tenant_uuid})
        return qs

    def perform_create(self, serializer):
        tenant_uuid = getattr(self.request, 'current_tenant_uuid', None)
        if tenant_uuid and hasattr(serializer.Meta.model, 'TenantUUID'):
            serializer.save(TenantUUID=tenant_uuid)
        else:
            serializer.save()


class UserScopedMixin:
    """ViewSet Mixin：自动按当前用户过滤 queryset（用户级数据隔离）

    用于无 CompanyUUID/TenantUUID 字段的用户级表（如 Backpack/Message/WorkOrder/Asset/Liability），
    普通用户只能看自己的数据，管理员（admin:super 或 admin:tenant）看全部。

    使用方式：
        class BackpackViewSet(UserScopedMixin, viewsets.ModelViewSet):
            user_field = 'UserUUID'  # FK 字段名（自动追加 _id）或 UUIDField 名
    """
    user_field = 'UserUUID'  # 可在 ViewSet 中覆盖

    def _is_admin_scope_disabled(self):
        """判断是否为管理员特权模式（委托给模块级 IsAdminScopeDisabled）"""
        return IsAdminScopeDisabled(getattr(self, 'request', None))

    def get_queryset(self):
        qs = super().get_queryset()
        if self._is_admin_scope_disabled():
            return qs
        user = getattr(self.request, 'user', None) if hasattr(self, 'request') else None
        if not user or not getattr(user, 'is_authenticated', False):
            return qs.none()
        user_uuid = getattr(user, 'UserUUID', None)
        if user_uuid is None:
            return qs.none()
        # 统一转 bytes（UUIDObj → bytes）
        user_uuid = user_uuid.bytes if hasattr(user_uuid, 'bytes') else user_uuid
        # 判断字段类型：ForeignKey 用 _id 后缀，UUIDField 直接用字段名
        fk_attr = self.user_field + '_id'
        if hasattr(qs.model, fk_attr):
            qs = qs.filter(**{fk_attr: user_uuid})
        elif hasattr(qs.model, self.user_field):
            qs = qs.filter(**{self.user_field: user_uuid})
        return qs


_PERM_CACHE_KEY = '_gvsdsdk_perm_verifier_cache'
_ROLE_INACTIVE = object()  # 哨兵：角色未启用，恒拒绝


def PermissionVerifier(permission, attributes, user=None, request=None):
    """权限验证器——混合 ABAC + RBAC 检查

    RBAC 仅检查当前角色（CurrentRoleUUID Cookie 指定的角色）的权限，
    通过 RolePermission → Permission 链路查询，按当前公司过滤。
    管理员（当前角色拥有 admin:super 权限）直接放行。

    性能优化：ABAC 权限标识集合与 RBAC 权限码集合在首次调用时预取，
    并挂载到 request 对象上作为请求级缓存，后续同一请求内的多次调用
    直接命中内存集合，将原本 3N 次查询降为常量次查询，消除 N+1 问题。
    """
    if not permission:
        return True
    if user is None:
        return False
    if not isinstance(user, _GetUserModel()):
        return False

    # 管理员（当前角色拥有 admin:super 权限）直接放行
    # 采用角色超级管理员模式：超级用户切换到普通角色时也需通过 RBAC 校验
    from gvsdsdk.auth.admin_service import IsAdmin
    if IsAdmin(request=request, user=user):
        return True

    current_role = getattr(request, 'current_role', None) if request else None
    current_company = getattr(request, 'current_company_uuid', None) if request else None

    # 请求级缓存：ABAC/RBAC 预取结果挂到 request 对象，同一请求内复用
    cache = getattr(request, _PERM_CACHE_KEY, None) if request is not None else None
    if cache is None:
        cache = {'abac': None, 'rbac': None}
        if request is not None:
            setattr(request, _PERM_CACHE_KEY, cache)

    # 预取 ABAC 权限标识集合（仅一次/请求）
    if cache['abac'] is None:
        cache['abac'] = set(UserABAC.objects.filter(
            UserUUID=user.UserUUID
        ).values_list('HierarchicalPermissionIdentifiers', flat=True))
    abac_attrs = cache['abac']

    # 预取 RBAC 权限码集合（仅一次/请求）
    if cache['rbac'] is None:
        if current_role and isinstance(current_role, Role):
            if current_role.RoleStatus != 1:
                cache['rbac'] = _ROLE_INACTIVE
            else:
                rp_qs = RolePermission.objects.filter(RoleUUID=current_role.RoleUUID)
                if current_company:
                    rp_qs = rp_qs.filter(CompanyUUID=current_company)
                perm_uuids = rp_qs.values_list('PermUUID', flat=True)
                cache['rbac'] = set(Permission.objects.filter(
                    PermUUID__in=perm_uuids,
                    PermStatus=1,
                ).values_list('PermCode', flat=True))
        else:
            # 无 current_role 时回退到用户所有活跃角色
            user_role_uuids = UserRole.objects.filter(
                UserUUID=user.UserUUID
            ).values_list('RoleUUID', flat=True)
            active_role_uuids = Role.objects.filter(
                RoleUUID__in=user_role_uuids,
                RoleStatus=1,
            ).values_list('RoleUUID', flat=True)
            rp_qs = RolePermission.objects.filter(RoleUUID__in=active_role_uuids)
            if current_company:
                rp_qs = rp_qs.filter(CompanyUUID=current_company)
            perm_uuids = rp_qs.values_list('PermUUID', flat=True)
            cache['rbac'] = set(Permission.objects.filter(
                PermUUID__in=perm_uuids,
                PermStatus=1,
            ).values_list('PermCode', flat=True))
    rbac_codes = cache['rbac']

    # 角色未启用哨兵，恒拒绝
    if rbac_codes is _ROLE_INACTIVE:
        return False

    for attr in attributes:
        # ABAC 命中即放行
        if attr in abac_attrs:
            continue
        # RBAC 命中即放行
        if attr in rbac_codes:
            continue
        return False
    return True

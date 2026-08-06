import uuid
from django.utils import timezone as _timezone

from gvsdsdk.models import Role, UserRole, RolePermission, Permission
from gvsdsdk.model_utils import _GetUserModel

# NOTE: 类型注解暂缓——RoleManager 静态方法较多，全量补充注解工作量较大且易引入回归，后续单独迭代处理。


class RoleManager:
    """角色管理器——提供角色创建、分配、权限管理"""

    @staticmethod
    def CreateRole(Name, TenantUUID, AssignScope=0, AssignScopeUUID=None, RoleType=2):
        """创建角色"""
        role = Role(
            RoleUUID=uuid.uuid4().bytes,
            TenantUUID=TenantUUID,
            RoleName=Name,
            RoleType=RoleType,
            RoleStatus=1,
            AssignScope=AssignScope,
            AssignScopeUUID=AssignScopeUUID,
            RoleDesc='',
            CreateTime=_timezone.now(),
        )
        role.save()
        return role

    @staticmethod
    def GetRoleByName(Name, TenantUUID=None):
        """按名称查询角色（同租户内角色名唯一）

        Args:
            Name: 角色名称
            TenantUUID: 可选，限定租户范围

        Returns:
            ``Role`` 实例；不存在则返回 ``None``
        """
        qs = Role.objects.filter(RoleName=Name)
        if TenantUUID is not None:
            qs = qs.filter(TenantUUID=TenantUUID)
        return qs.first()

    @staticmethod
    def HasRole(UserUUID, RoleUUID, CompanyUUID=None):
        """检查用户是否已拥有指定角色（同公司下防重复绑定）

        Args:
            UserUUID: 用户 UUID
            RoleUUID: 角色 UUID
            CompanyUUID: 可选，限定公司范围

        Returns:
            True 如果已存在该 user-role-company 三元关联；False 否则
        """
        qs = UserRole.objects.filter(UserUUID=UserUUID, RoleUUID=RoleUUID)
        if CompanyUUID is not None:
            qs = qs.filter(CompanyUUID=CompanyUUID)
        return qs.exists()

    @staticmethod
    def AssignRole(UserUUID, RoleUUID, CompanyUUID, RoleName=None):
        """为用户分配角色

        Args:
            UserUUID: 用户 UUID
            RoleUUID: 角色 UUID
            CompanyUUID: 公司 UUID
            RoleName: 可选，该用户-角色关联的角色名覆盖（如 "枢机协律玩家 - WYT2WYT"）；
                为 None 时前端展示回退到 Role.RoleName
        """
        user_role = UserRole(
            UserRoleUUID=uuid.uuid4().bytes,
            UserUUID=UserUUID,
            RoleUUID=RoleUUID,
            CompanyUUID=CompanyUUID,
            RoleName=RoleName,
            CreateTime=_timezone.now(),
        )
        user_role.save()
        return user_role

    @staticmethod
    def RemoveRole(UserUUID, RoleUUID, CompanyUUID=None):
        """移除用户的指定角色"""
        qs = UserRole.objects.filter(UserUUID=UserUUID, RoleUUID=RoleUUID)
        if CompanyUUID is not None:
            qs = qs.filter(CompanyUUID=CompanyUUID)
        deleted, _ = qs.delete()
        return deleted > 0

    @staticmethod
    def GetRolesForUser(UserUUID, CompanyUUID=None):
        """获取用户的角色"""
        qs = UserRole.objects.filter(UserUUID=UserUUID)
        if CompanyUUID is not None:
            qs = qs.filter(CompanyUUID=CompanyUUID)
        role_uuids = qs.values_list('RoleUUID', flat=True)
        return Role.objects.filter(RoleUUID__in=role_uuids)

    @staticmethod
    def GetUsersForRole(RoleUUID):
        """获取拥有指定角色的所有用户"""
        User = _GetUserModel()
        user_uuids = UserRole.objects.filter(RoleUUID=RoleUUID).values_list('UserUUID', flat=True)
        return User.objects.filter(UserUUID__in=user_uuids)

    @staticmethod
    def AssignPermission(RoleUUID, PermUUID, CompanyUUID):
        """为角色分配权限（指定公司）"""
        rp = RolePermission(
            RolePermUUID=uuid.uuid4().bytes,
            RoleUUID=RoleUUID,
            PermUUID=PermUUID,
            CompanyUUID=CompanyUUID,
            CreateTime=_timezone.now(),
        )
        rp.save()
        return rp

    @staticmethod
    def RemovePermission(RoleUUID, PermUUID, CompanyUUID=None):
        """移除角色的指定权限"""
        qs = RolePermission.objects.filter(RoleUUID=RoleUUID, PermUUID=PermUUID)
        if CompanyUUID is not None:
            qs = qs.filter(CompanyUUID=CompanyUUID)
        deleted, _ = qs.delete()
        return deleted > 0

    @staticmethod
    def GetPermissionsForRole(RoleUUID, CompanyUUID=None):
        """获取角色的所有权限（可按公司过滤）"""
        qs = RolePermission.objects.filter(RoleUUID=RoleUUID)
        if CompanyUUID is not None:
            qs = qs.filter(CompanyUUID=CompanyUUID)
        perm_uuids = qs.values_list('PermUUID', flat=True)
        return Permission.objects.filter(PermUUID__in=perm_uuids, PermStatus=1)


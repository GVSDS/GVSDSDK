import uuid
from django.utils import timezone as _timezone

from gvsdsdk.models import Role, UserRole, RolePermission, Permission, Shop, RoleShop, Position, PositionTemplate, PositionTemplatePermission
from gvsdsdk.model_utils import _GetUserModel

# NOTE: 类型注解（L8）暂缓——本模块含 RoleManager/ShopManager/PositionManager 共 20+ 静态方法，
# 全量补充参数与返回值注解工作量较大且易引入回归，后续单独迭代处理。


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


class ShopManager:
    """商铺管理器——提供商铺创建、角色关联、查询"""

    @staticmethod
    def CreateShop(Name, CompanyUUID, Code='', ShopType=0, **Kwargs):
        """创建商铺"""
        shop = Shop(
            ShopUUID=uuid.uuid4().bytes,
            CompanyUUID=CompanyUUID,
            ShopName=Name,
            ShopCode=Code,
            ShopType=ShopType,
            ShopStatus=1,
            ShopAddress=Kwargs.get('ShopAddress', ''),
            ShopContact=Kwargs.get('ShopContact', ''),
            ShopPhone=Kwargs.get('ShopPhone', ''),
            ShopDesc=Kwargs.get('ShopDesc', ''),
            CreateTime=_timezone.now(),
            UpdateTime=_timezone.now(),
        )
        shop.save()
        return shop

    @staticmethod
    def AssignRole(RoleUUID, ShopUUID, IsMainShop=False):
        """为角色关联商铺"""
        role_shop = RoleShop(
            RoleShopUUID=uuid.uuid4().bytes,
            RoleUUID=RoleUUID,
            ShopUUID=ShopUUID,
            IsMainShop=1 if IsMainShop else 0,
            CreateTime=_timezone.now(),
        )
        role_shop.save()
        return role_shop

    @staticmethod
    def RemoveRole(RoleUUID, ShopUUID):
        """移除角色与商铺的关联"""
        deleted, _ = RoleShop.objects.filter(RoleUUID=RoleUUID, ShopUUID=ShopUUID).delete()
        return deleted > 0

    @staticmethod
    def GetShopsForRole(RoleUUID):
        """获取角色关联的所有商铺"""
        shop_uuids = RoleShop.objects.filter(RoleUUID=RoleUUID).values_list('ShopUUID', flat=True)
        return Shop.objects.filter(ShopUUID__in=shop_uuids)

    @staticmethod
    def GetRolesForShop(ShopUUID):
        """获取商铺关联的所有角色"""
        role_uuids = RoleShop.objects.filter(ShopUUID=ShopUUID).values_list('RoleUUID', flat=True)
        return Role.objects.filter(RoleUUID__in=role_uuids)

    @staticmethod
    def GetMainShopForRole(RoleUUID):
        """获取角色的主商铺"""
        rs = RoleShop.objects.filter(RoleUUID=RoleUUID, IsMainShop=1).first()
        if rs:
            return Shop.objects.filter(ShopUUID=rs.ShopUUID).first()
        return None

    @staticmethod
    def GetShopsForCompany(CompanyUUID):
        """获取公司下的所有商铺"""
        return Shop.objects.filter(CompanyUUID=CompanyUUID, ShopStatus=1)


class PositionManager:
    """职务管理器——提供人事职务标签创建、岗位模板管理"""

    @staticmethod
    def CreatePosition(Name, TenantUUID, Code='', Level=0, **Kwargs):
        """创建人事职务标签"""
        position = Position(
            PositionUUID=uuid.uuid4().bytes,
            TenantUUID=TenantUUID,
            PositionName=Name,
            PositionCode=Code,
            PositionLevel=Level,
            PositionStatus=1,
            PositionDesc=Kwargs.get('PositionDesc', ''),
            CreateTime=_timezone.now(),
            UpdateTime=_timezone.now(),
        )
        position.save()
        return position

    @staticmethod
    def GetPositionsForTenant(TenantUUID):
        """获取租户下的所有职务"""
        return Position.objects.filter(TenantUUID=TenantUUID, PositionStatus=1)

    @staticmethod
    def CreatePositionTemplate(Name, TenantUUID, Code='', **Kwargs):
        """创建岗位模板"""
        template = PositionTemplate(
            PositionTemplateUUID=uuid.uuid4().bytes,
            TenantUUID=TenantUUID,
            TemplateName=Name,
            TemplateCode=Code,
            TemplateStatus=1,
            TemplateDesc=Kwargs.get('TemplateDesc', ''),
            CreateTime=_timezone.now(),
            UpdateTime=_timezone.now(),
        )
        template.save()
        return template

    @staticmethod
    def AssignPermToTemplate(TemplateUUID, PermUUID):
        """为岗位模板关联权限"""
        ptp = PositionTemplatePermission(
            PTPermUUID=uuid.uuid4().bytes,
            PositionTemplateUUID=TemplateUUID,
            PermUUID=PermUUID,
            CreateTime=_timezone.now(),
        )
        ptp.save()
        return ptp

    @staticmethod
    def RemovePermFromTemplate(TemplateUUID, PermUUID):
        """移除岗位模板与权限的关联"""
        deleted, _ = PositionTemplatePermission.objects.filter(
            PositionTemplateUUID=TemplateUUID, PermUUID=PermUUID
        ).delete()
        return deleted > 0

    @staticmethod
    def GetPermsForTemplate(TemplateUUID):
        """获取岗位模板关联的所有权限"""
        perm_uuids = PositionTemplatePermission.objects.filter(
            PositionTemplateUUID=TemplateUUID
        ).values_list('PermUUID', flat=True)
        return Permission.objects.filter(PermUUID__in=perm_uuids, PermStatus=1)

    @staticmethod
    def GetTemplatesForTenant(TenantUUID):
        """获取租户下的所有岗位模板"""
        return PositionTemplate.objects.filter(TenantUUID=TenantUUID, TemplateStatus=1)

    @staticmethod
    def CopyTemplateToRole(TemplateUUID, RoleName, TenantUUID):
        """从岗位模板复制权限到新角色"""
        template = PositionTemplate.objects.get(PositionTemplateUUID=TemplateUUID)
        return template.CopyToRole(RoleName, TenantUUID)

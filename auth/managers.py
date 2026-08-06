import uuid
from django.utils import timezone as _timezone

from gvsdsdk.models import Role, UserRole, RolePermission, Permission, Position, PositionTemplate, PositionTemplatePermission
from gvsdsdk.model_utils import _GetUserModel

# NOTE: 类型注解暂缓——RoleManager/PositionManager 静态方法较多，全量补充注解工作量较大且易引入回归，后续单独迭代处理。


class RoleManager:
    """角色管理器——提供角色创建、分配、权限管理"""

    @staticmethod
    def CreateRole(Name, TenantUUID, AssignScope=0, AssignScopeUUID=None, RoleType=2, RoleDesc=''):
        """创建角色"""
        role = Role(
            RoleUUID=uuid.uuid4().bytes,
            TenantUUID=TenantUUID,
            RoleName=Name,
            RoleType=RoleType,
            RoleStatus=1,
            AssignScope=AssignScope,
            AssignScopeUUID=AssignScopeUUID,
            RoleDesc=RoleDesc,
            CreateTime=_timezone.now(),
        )
        role.save()
        return role

    @staticmethod
    def GetRoleByUUID(RoleUUID):
        """按 UUID 查询角色（不存在返回 None）"""
        return Role.objects.filter(RoleUUID=RoleUUID).first()

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

    @staticmethod
    def GetUserRoleDetails(UserUUID, CompanyUUID=None):
        """获取用户的 UserRole 关联详情（按行返回，保留行级信息）

        与 GetRolesForUser（仅返回 Role）互补：本方法保留 UserRole 的
        CompanyUUID、RoleName 覆盖、RoleType、UserRoleUUID 等行级字段，
        供需要展示"角色+公司+覆盖名"等富信息的场景使用。

        Returns:
            list[UserRole]：UserRole 实例列表（保持数据库顺序）
        """
        qs = UserRole.objects.filter(UserUUID=UserUUID)
        if CompanyUUID is not None:
            qs = qs.filter(CompanyUUID=CompanyUUID)
        return list(qs)

    @staticmethod
    def HasRolePermission(RoleUUID, PermUUID, CompanyUUID=None):
        """检查角色是否已拥有指定权限（同公司下防重复绑定）"""
        qs = RolePermission.objects.filter(RoleUUID=RoleUUID, PermUUID=PermUUID)
        if CompanyUUID is not None:
            qs = qs.filter(CompanyUUID=CompanyUUID)
        return qs.exists()


class PositionManager:
    """职务管理器——提供人事职务标签创建、岗位模板管理"""

    # ── 人事职务标签 ──

    @staticmethod
    def CreatePosition(Name, TenantUUID, Code='', Level=0, **Kwargs):
        """创建人事职务标签（TenantUUID 传 UUIDObj/str/bytes）"""
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
        """获取租户下的所有启用职务"""
        return Position.objects.filter(TenantUUID=TenantUUID, PositionStatus=1)

    @staticmethod
    def GetPositionByUUID(PositionUUID):
        """按 UUID 查询职务（不存在返回 None）"""
        return Position.objects.filter(PositionUUID=PositionUUID).first()

    @staticmethod
    def UpdatePosition(PositionUUID, **Fields):
        """更新职务字段（自动刷新 UpdateTime）

        Args:
            PositionUUID: 职务 UUID
            **Fields: 允许字段 PositionName/PositionCode/PositionLevel/PositionStatus/PositionDesc

        Returns:
            更新后的 Position 实例；职务不存在返回 None
        """
        position = Position.objects.filter(PositionUUID=PositionUUID).first()
        if not position:
            return None
        allowed = ('PositionName', 'PositionCode', 'PositionLevel', 'PositionStatus', 'PositionDesc')
        update_fields = []
        for key, value in Fields.items():
            if key in allowed:
                setattr(position, key, value)
                update_fields.append(key)
        position.UpdateTime = _timezone.now()
        update_fields.append('UpdateTime')
        if update_fields:
            position.save(update_fields=update_fields)
        return position

    @staticmethod
    def DeletePosition(PositionUUID):
        """软删除职务（PositionStatus=0）；返回是否命中"""
        position = Position.objects.filter(PositionUUID=PositionUUID).first()
        if not position:
            return False
        position.PositionStatus = 0
        position.UpdateTime = _timezone.now()
        position.save(update_fields=['PositionStatus', 'UpdateTime'])
        return True

    # ── 岗位模板 ──

    @staticmethod
    def CreatePositionTemplate(Name, TenantUUID, Code='', **Kwargs):
        """创建岗位模板（TenantUUID 传 UUIDObj/str/bytes）"""
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
    def GetTemplatesForTenant(TenantUUID):
        """获取租户下的所有启用岗位模板"""
        return PositionTemplate.objects.filter(TenantUUID=TenantUUID, TemplateStatus=1)

    @staticmethod
    def GetPositionTemplateByUUID(PositionTemplateUUID):
        """按 UUID 查询岗位模板（不存在返回 None）"""
        return PositionTemplate.objects.filter(PositionTemplateUUID=PositionTemplateUUID).first()

    @staticmethod
    def UpdatePositionTemplate(PositionTemplateUUID, **Fields):
        """更新岗位模板字段（自动刷新 UpdateTime）

        Args:
            PositionTemplateUUID: 岗位模板 UUID
            **Fields: 允许字段 TemplateName/TemplateCode/TemplateStatus/TemplateDesc

        Returns:
            更新后的 PositionTemplate 实例；模板不存在返回 None
        """
        template = PositionTemplate.objects.filter(PositionTemplateUUID=PositionTemplateUUID).first()
        if not template:
            return None
        allowed = ('TemplateName', 'TemplateCode', 'TemplateStatus', 'TemplateDesc')
        update_fields = []
        for key, value in Fields.items():
            if key in allowed:
                setattr(template, key, value)
                update_fields.append(key)
        template.UpdateTime = _timezone.now()
        update_fields.append('UpdateTime')
        if update_fields:
            template.save(update_fields=update_fields)
        return template

    @staticmethod
    def DeletePositionTemplate(PositionTemplateUUID):
        """软删除岗位模板（TemplateStatus=0）；返回是否命中"""
        template = PositionTemplate.objects.filter(PositionTemplateUUID=PositionTemplateUUID).first()
        if not template:
            return False
        template.TemplateStatus = 0
        template.UpdateTime = _timezone.now()
        template.save(update_fields=['TemplateStatus', 'UpdateTime'])
        return True

    # ── 岗位模板权限关联 ──

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
        """移除岗位模板与权限的关联；返回是否命中"""
        deleted, _ = PositionTemplatePermission.objects.filter(
            PositionTemplateUUID=TemplateUUID, PermUUID=PermUUID
        ).delete()
        return deleted > 0

    @staticmethod
    def GetPermsForTemplate(TemplateUUID):
        """获取岗位模板关联的所有启用权限"""
        perm_uuids = PositionTemplatePermission.objects.filter(
            PositionTemplateUUID=TemplateUUID
        ).values_list('PermUUID', flat=True)
        return Permission.objects.filter(PermUUID__in=perm_uuids, PermStatus=1)

    @staticmethod
    def HasTemplatePerm(TemplateUUID, PermUUID):
        """检查岗位模板是否已关联指定权限"""
        return PositionTemplatePermission.objects.filter(
            PositionTemplateUUID=TemplateUUID, PermUUID=PermUUID,
        ).exists()


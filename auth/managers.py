import datetime
import uuid
from django.db.models import Q
from django.utils import timezone as _timezone

from gvsdsdk.models import Role, UserRole, UserRoleDepartment, UserRoleDepartmentPosition, UserRoleWorkgroup, RolePermission, Permission, Position, PositionCategory, PositionTemplate, PositionTemplatePermission, AppPosition, AppVirtualPosition, AppVirtualBinding, App, TenantApp, CompanyApp, UserApp, AppConfig, Shop, RoleShop, User2FA, UserAvatar, Password, UserPending2FA, AccountActivation, Tenant, Company, Department, DepartmentSMTM, Workgroup, Message, Menu, WorkOrder, AuditLog, ItemReference, Backpack, FileAsset, CompanyInfo
from gvsdsdk.auth.crypto import HashPassword, VerifyPassword
from gvsdsdk.model_utils import _GetUserModel, UUIDObj

# 系统公司 UUID（00000000-0000-0000-0000-000000000001），全局唯一
_SYSTEM_COMPANY_UUID = uuid.UUID('00000000-0000-0000-0000-000000000001').bytes

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
    def GetCompanyUserUUIDs(CompanyUUID):
        """获取公司下所有用户 UUID（通过 UserRole 关联，去重）

        Args:
            CompanyUUID: 公司 UUID（bytes/str/UUIDObj 均可）

        Returns:
            set[bytes]：公司关联用户 UUID 集合
        """
        return set(
            UserRole.objects.filter(CompanyUUID=UUIDObj(CompanyUUID).bytes)
            .values_list('UserUUID', flat=True)
        )

    @staticmethod
    def QueryUserRolesByCompany(CompanyUUID):
        """按公司查询用户-角色关联（CompanyUUID 缺失或非法返回空集）

        供视图层在「显式指定公司」时强制收窄数据范围（超管也不豁免）。
        """
        if not CompanyUUID:
            return UserRole.objects.none()
        try:
            comp_bytes = UUIDObj(CompanyUUID).bytes
        except Exception:
            return UserRole.objects.none()
        return UserRole.objects.filter(CompanyUUID=comp_bytes)

    @staticmethod
    def GetUserRolesByUUIDs(UserRoleUUIDs):
        """批量按 UUID 取「用户-角色」关联行（无效项自动跳过）

        Args:
            UserRoleUUIDs: UserRoleUUID 集合（bytes/str/UUIDObj 均可）

        Returns:
            list[UserRole]
        """
        uuids = []
        for u in (UserRoleUUIDs or []):
            if not u:
                continue
            try:
                uuids.append(UUIDObj(u).bytes)
            except Exception:
                continue
        if not uuids:
            return []
        return list(UserRole.objects.filter(UserRoleUUID__in=uuids))

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
        CompanyUUID、RoleName 覆盖、UserRoleUUID 等行级字段，
        供需要展示"角色+公司+覆盖名"等富信息的场景使用。

        注意：UserRole 的部门/工作组归属与成员类型（RoleType）已废弃，
        一律以 UserRoleDepartment / UserRoleWorkgroup 关联表为准。

        Returns:
            list[UserRole]：UserRole 实例列表（保持数据库顺序）
        """
        qs = UserRole.objects.filter(UserUUID=UserUUID)
        if CompanyUUID is not None:
            qs = qs.filter(CompanyUUID=CompanyUUID)
        return list(qs)

    @staticmethod
    def GetUserRoleRelation(UserUUID, RoleUUID, CompanyUUID=None):
        """按 (用户, 角色[, 公司]) 查询 UserRole 关联（不存在返回 None）"""
        qs = UserRole.objects.filter(
            UserUUID=UUIDObj(UserUUID).bytes,
            RoleUUID=UUIDObj(RoleUUID).bytes,
        )
        if CompanyUUID is not None:
            try:
                qs = qs.filter(CompanyUUID=UUIDObj(CompanyUUID).bytes)
            except Exception:
                qs = qs.filter(CompanyUUID=CompanyUUID)
        return qs.first()

    @staticmethod
    def GetUserRoleDepartment(UserRoleUUID):
        """按 UserRoleUUID 查询部门归属（不存在返回 None）"""
        return UserRoleDepartment.objects.filter(
            UserRoleUUID=UUIDObj(UserRoleUUID).bytes,
        ).first()

    @staticmethod
    def GetUserRoleWorkgroup(UserRoleUUID):
        """按 UserRoleUUID 查询工作组归属（不存在返回 None）"""
        return UserRoleWorkgroup.objects.filter(
            UserRoleUUID=UUIDObj(UserRoleUUID).bytes,
        ).first()

    @staticmethod
    def GetUserRoleByUUID(UserRoleUUID):
        """按 UserRoleUUID 查询「用户-角色」关联行（不存在返回 None）"""
        if not UserRoleUUID:
            return None
        return UserRole.objects.filter(UserRoleUUID=UUIDObj(UserRoleUUID).bytes).first()

    @staticmethod
    def GetUserRoleDepartmentForCompany(UserRoleUUID, CompanyUUID=None):
        """按 (UserRoleUUID[, CompanyUUID]) 查询部门归属（不存在返回 None）"""
        if not UserRoleUUID:
            return None
        qs = UserRoleDepartment.objects.filter(UserRoleUUID=UUIDObj(UserRoleUUID).bytes)
        if CompanyUUID is not None:
            try:
                qs = qs.filter(CompanyUUID=UUIDObj(CompanyUUID).bytes)
            except Exception:
                qs = qs.filter(CompanyUUID=CompanyUUID)
        return qs.first()

    @staticmethod
    def EnsureUserRoleDepartment(UserRoleUUID, DeptUUID, TenantUUID=None, CompanyUUID=None,
                                 RoleType=0, MatchCompany=False):
        """把「用户-角色」关联的部门归属建好或改到目标部门

        「用户-角色」与部门是一对一主关联（同一 UserRoleUUID 只保留一条 URD 行），
        故存在则改 DeptUUID，不存在则新建。

        Args:
            UserRoleUUID: 用户-角色关联 UUID
            DeptUUID: 目标部门 UUID
            TenantUUID/CompanyUUID: 新建时的归属，可按需传入
            RoleType: 角色类型（默认 0）
            MatchCompany: True 时按 (UserRoleUUID, CompanyUUID) 定位既有行（多公司各自成行），
                          False（默认）只按 UserRoleUUID 定位

        Returns:
            UserRoleDepartment 实例
        """
        urd = RoleManager.GetUserRoleDepartmentForCompany(
            UserRoleUUID, CompanyUUID if MatchCompany else None,
        )
        dept_bytes = UUIDObj(DeptUUID).bytes
        if urd is None:
            return UserRoleDepartment.objects.create(
                UserRoleDepartmentUUID=uuid.uuid4().bytes,
                UserRoleUUID=UUIDObj(UserRoleUUID).bytes,
                DeptUUID=dept_bytes,
                TenantUUID=TenantUUID,
                CompanyUUID=CompanyUUID,
                RoleType=RoleType,
            )
        urd.DeptUUID = dept_bytes
        urd.save(update_fields=['DeptUUID'])
        return urd

    @staticmethod
    def HasUserRoleDepartment(UserRoleUUID, DeptUUID, CompanyUUID=None):
        """该「用户-角色」关联是否已归属指定部门（可按公司收窄）"""
        qs = UserRoleDepartment.objects.filter(
            UserRoleUUID=UUIDObj(UserRoleUUID).bytes,
            DeptUUID=UUIDObj(DeptUUID).bytes,
        )
        if CompanyUUID is not None:
            try:
                qs = qs.filter(CompanyUUID=UUIDObj(CompanyUUID).bytes)
            except Exception:
                qs = qs.filter(CompanyUUID=CompanyUUID)
        return qs.exists()

    @staticmethod
    def HasUserRoleWorkgroup(UserRoleUUID, WorkgroupUUID, CompanyUUID=None):
        """该「用户-角色」关联是否已归属指定工作组（可按公司收窄）"""
        qs = UserRoleWorkgroup.objects.filter(
            UserRoleUUID=UUIDObj(UserRoleUUID).bytes,
            WorkgroupUUID=UUIDObj(WorkgroupUUID).bytes,
        )
        if CompanyUUID is not None:
            try:
                qs = qs.filter(CompanyUUID=UUIDObj(CompanyUUID).bytes)
            except Exception:
                qs = qs.filter(CompanyUUID=CompanyUUID)
        return qs.exists()

    @staticmethod
    def UserRoleRelationExists(UserUUID, RoleUUID, ExcludeUserRoleUUID=None, CompanyUUID=None):
        """该 (用户, 角色) 关联是否已存在（供表单编辑查重）

        Args:
            UserUUID/RoleUUID: 目标用户与角色
            ExcludeUserRoleUUID: 排除自身记录（编辑场景必传，否则会与自身冲突）
            CompanyUUID: 可选，限定公司范围
        """
        qs = UserRole.objects.filter(
            UserUUID=UUIDObj(UserUUID).bytes,
            RoleUUID=UUIDObj(RoleUUID).bytes,
        )
        if CompanyUUID is not None:
            try:
                qs = qs.filter(CompanyUUID=UUIDObj(CompanyUUID).bytes)
            except Exception:
                qs = qs.filter(CompanyUUID=CompanyUUID)
        if ExcludeUserRoleUUID is not None:
            qs = qs.exclude(UserRoleUUID=UUIDObj(ExcludeUserRoleUUID).bytes)
        return qs.exists()

    @staticmethod
    def _NormalizeUUIDs(Values):
        """UUID 集合归一化为 bytes 列表（无效项跳过；无有效值返回空列表）"""
        uuids = []
        for v in (Values or []):
            if not v:
                continue
            try:
                uuids.append(UUIDObj(v).bytes)
            except Exception:
                continue
        return uuids

    @staticmethod
    def GetUserRoleDepartmentsByUUIDs(UserRoleDepartmentUUIDs):
        """批量按 URD 主键取部门归属行"""
        uuids = RoleManager._NormalizeUUIDs(UserRoleDepartmentUUIDs)
        if not uuids:
            return []
        return list(UserRoleDepartment.objects.filter(UserRoleDepartmentUUID__in=uuids))

    @staticmethod
    def GetUserRoleDepartmentsByDeptUUIDs(DeptUUIDs):
        """批量按部门取部门归属行（部门列表页成员）"""
        uuids = RoleManager._NormalizeUUIDs(DeptUUIDs)
        if not uuids:
            return []
        return list(UserRoleDepartment.objects.filter(DeptUUID__in=uuids))

    @staticmethod
    def GetUserRoleWorkgroupsByUUIDs(UserRoleWorkgroupUUIDs):
        """批量按 URW 主键取工作组归属行"""
        uuids = RoleManager._NormalizeUUIDs(UserRoleWorkgroupUUIDs)
        if not uuids:
            return []
        return list(UserRoleWorkgroup.objects.filter(UserRoleWorkgroupUUID__in=uuids))

    @staticmethod
    def GetUserRoleWorkgroupsByWorkgroupUUIDs(WorkgroupUUIDs):
        """批量按工作组取工作组归属行（工作组列表页成员）"""
        uuids = RoleManager._NormalizeUUIDs(WorkgroupUUIDs)
        if not uuids:
            return []
        return list(UserRoleWorkgroup.objects.filter(WorkgroupUUID__in=uuids))

    @staticmethod
    def GetUserRoleDepartments(UserRoleUUID, CompanyUUID=None, RoleType=None):
        """按 UserRoleUUID 查询部门归属列表（一个用户-角色对可挂多个部门）

        Args:
            UserRoleUUID: 用户-角色关联 UUID
            CompanyUUID: 公司 UUID（跨公司隔离：与 UserRole 必须同公司）
            RoleType: 成员类型过滤（0=成员，1=负责人）；负责人判定以关联表为准

        Returns:
            list[UserRoleDepartment]：关联行列表（保持数据库顺序）
        """
        qs = UserRoleDepartment.objects.filter(
            UserRoleUUID=UUIDObj(UserRoleUUID).bytes,
        )
        if CompanyUUID is not None:
            try:
                qs = qs.filter(CompanyUUID=UUIDObj(CompanyUUID).bytes)
            except Exception:
                qs = qs.filter(CompanyUUID=CompanyUUID)
        if RoleType is not None:
            qs = qs.filter(RoleType=RoleType)
        return list(qs)

    @staticmethod
    def GetUserRoleWorkgroups(UserRoleUUID, CompanyUUID=None, RoleType=None):
        """按 UserRoleUUID 查询工作组归属列表（一个用户-角色对可挂多个工作组）"""
        qs = UserRoleWorkgroup.objects.filter(
            UserRoleUUID=UUIDObj(UserRoleUUID).bytes,
        )
        if CompanyUUID is not None:
            try:
                qs = qs.filter(CompanyUUID=UUIDObj(CompanyUUID).bytes)
            except Exception:
                qs = qs.filter(CompanyUUID=CompanyUUID)
        if RoleType is not None:
            qs = qs.filter(RoleType=RoleType)
        return list(qs)

    @staticmethod
    def GetScopeMembers(ScopeType, UUID):
        """获取作用域成员的用户 UUID 列表（去重）

        Args:
            ScopeType: 'dept_manager' | 'workgroup_lead'
            UUID: 部门或工作组 UUID（bytes/str/UUIDObj 均可）

        Returns:
            list[bytes]：成员用户 UUID；无成员返回 []；未知类型或异常返回 None
        """
        try:
            uuid_bytes = UUIDObj(UUID).bytes
        except Exception:
            return None
        try:
            if ScopeType == 'dept_manager':
                ur_uuids = list(UserRoleDepartment.objects.filter(
                    DeptUUID=uuid_bytes,
                ).values_list('UserRoleUUID', flat=True))
            elif ScopeType == 'workgroup_lead':
                ur_uuids = list(UserRoleWorkgroup.objects.filter(
                    WorkgroupUUID=uuid_bytes,
                ).values_list('UserRoleUUID', flat=True))
            else:
                return None
            if not ur_uuids:
                return []
            # UserRoleUUID → UserUUID（关联表存 UserRoleUUID，用户 UUID 从 UserRole 表取）
            qs = UserRole.objects.filter(UserRoleUUID__in=ur_uuids)
            return list(set(qs.values_list('UserUUID', flat=True)))
        except Exception:
            return None

    @staticmethod
    def HasRolePermission(RoleUUID, PermUUID, CompanyUUID=None):
        """检查角色是否已拥有指定权限（同公司下防重复绑定）"""
        qs = RolePermission.objects.filter(RoleUUID=RoleUUID, PermUUID=PermUUID)
        if CompanyUUID is not None:
            qs = qs.filter(CompanyUUID=CompanyUUID)
        return qs.exists()

    @staticmethod
    def SaveRolePermissions(RoleUUID, CompanyUUID, PermUUIDs, Commit=True):
        """批量保存角色权限——按 RoleUUID+CompanyUUID 维度增量更新

        逻辑：不在目标列表中的现有关联删除；目标列表中未关联的新增；已存在的保留。

        Args:
            RoleUUID: 角色 UUID（bytes/str/UUIDObj 均可）
            CompanyUUID: 公司 UUID
            PermUUIDs: 目标权限 UUID 列表（bytes/str 均可，内部去重并过滤无效项）
            Commit: 是否包在事务中提交（默认 True）

        Returns:
            (Added, Removed, Kept, Total) 统计元组
        """
        target_perm_uuids = set()
        for p in PermUUIDs or []:
            try:
                target_perm_uuids.add(UUIDObj(p).bytes)
            except Exception:
                continue

        valid_perm_uuids = set(
            Permission.objects.filter(PermUUID__in=target_perm_uuids)
            .values_list('PermUUID', flat=True)
        ) if target_perm_uuids else set()

        def _apply():
            existing_qs = RolePermission.objects.filter(
                RoleUUID=RoleUUID, CompanyUUID=CompanyUUID,
            )
            existing_perm_uuids = set(existing_qs.values_list('PermUUID', flat=True))

            to_delete = existing_perm_uuids - valid_perm_uuids
            if to_delete:
                existing_qs.filter(PermUUID__in=to_delete).delete()

            to_add = valid_perm_uuids - existing_perm_uuids
            new_objs = [
                RolePermission(
                    RolePermUUID=uuid.uuid4().bytes,
                    RoleUUID=RoleUUID,
                    PermUUID=p_uuid,
                    CompanyUUID=CompanyUUID,
                )
                for p_uuid in to_add
            ]
            if new_objs:
                RolePermission.objects.bulk_create(new_objs)

            return (
                len(to_add),
                len(to_delete),
                len(existing_perm_uuids & valid_perm_uuids),
                len(valid_perm_uuids),
            )

        if Commit:
            from django.db import transaction
            with transaction.atomic():
                return _apply()
        return _apply()

    @staticmethod
    def CreatePermission(PermCode, TenantUUID, PermName='', PermCategory='',
                         PermStatus=1, PermDesc=''):
        """创建权限（同租户内权限码唯一）

        Args:
            PermCode: 权限码
            TenantUUID: 租户 UUID（UUIDObj/str/bytes 均可）
            PermName: 权限名称（为空时回退到权限码）
            PermCategory: 权限分类
            PermStatus: 状态（1=启用）
            PermDesc: 描述

        Returns:
            (Permission 实例, created: bool)；权限码已存在返回 (None, False)
        """
        if Permission.objects.filter(PermCode=PermCode, TenantUUID=TenantUUID).exists():
            return None, False
        perm = Permission(
            PermUUID=uuid.uuid4().bytes,
            TenantUUID=TenantUUID,
            PermCode=PermCode,
            PermName=PermName or PermCode,
            PermCategory=PermCategory,
            PermStatus=PermStatus,
            PermDesc=PermDesc,
        )
        perm.save()
        return perm, True

    @staticmethod
    def GetPermissionsTree(RoleUUID, CompanyUUID, TenantUUID=None):
        """获取角色权限树——按 PermCategory 分组，标记角色+公司下已勾选权限

        Args:
            RoleUUID: 角色 UUID
            CompanyUUID: 公司 UUID
            TenantUUID: 可选，租户 UUID（过滤权限范围；传 None 表示跨租户全量）

        Returns:
            list[dict]：分组列表，每组含 ``Category`` 与 ``Permissions``
            （每项含 PermUUID/PermName/PermCode/PermDesc/Checked）；未分类组排在最后
        """
        perm_qs = Permission.objects.filter(PermStatus=1)
        if TenantUUID is not None:
            perm_qs = perm_qs.filter(TenantUUID=TenantUUID)
        perm_qs = perm_qs.order_by('PermCategory', 'PermName')

        checked_perm_uuids = set(
            RolePermission.objects.filter(
                RoleUUID=RoleUUID, CompanyUUID=CompanyUUID,
            ).values_list('PermUUID', flat=True)
        )

        groups = {}
        for perm in perm_qs:
            category = perm.PermCategory or '未分类'
            if category not in groups:
                groups[category] = {'Category': category, 'Permissions': []}
            groups[category]['Permissions'].append({
                'PermUUID': UUIDObj(perm.PermUUID).str if perm.PermUUID else '',
                'PermName': perm.PermName,
                'PermCode': perm.PermCode,
                'PermDesc': perm.PermDesc or '',
                'Checked': perm.PermUUID in checked_perm_uuids,
            })

        result = [g for k, g in groups.items() if k != '未分类']
        if '未分类' in groups:
            result.append(groups['未分类'])
        return result

    @staticmethod
    def MatchUserRoleIds(Keyword):
        """按用户名或角色名匹配关键字，返回对应的 UserUUID/RoleUUID 集合

        供用户-角色关联搜索使用：先解析名称得到 UUID 集合，再在已隔离的
        queryset 上按集合过滤（隔离语义由调用方 queryset 保留）。

        Args:
            Keyword: 用户名/角色名关键字

        Returns:
            (user_uuids: set[bytes], role_uuids: set[bytes])
        """
        if not Keyword:
            return set(), set()
        User = _GetUserModel()
        matched_users = User.objects.filter(
            UserName__icontains=Keyword,
        ).values_list('UserUUID', flat=True)
        matched_roles = Role.objects.filter(
            RoleName__icontains=Keyword,
        ).values_list('RoleUUID', flat=True)
        user_uuids = set()
        role_uuids = set()
        for u in matched_users:
            try:
                user_uuids.add(UUIDObj(u).bytes)
            except (ValueError, AttributeError):
                pass
        for r in matched_roles:
            try:
                role_uuids.add(UUIDObj(r).bytes)
            except (ValueError, AttributeError):
                pass
        return user_uuids, role_uuids

    @staticmethod
    def GetPermissionByCode(PermCode, TenantUUID=None):
        """按权限码查询启用权限（存在多个取首个；不存在返回 None）"""
        if not PermCode:
            return None
        qs = Permission.objects.filter(PermCode=PermCode, PermStatus=1)
        if TenantUUID is not None:
            qs = qs.filter(TenantUUID=TenantUUID)
        return qs.first()

    @staticmethod
    def HasUserPermission(UserUUID, PermCode, CompanyUUID=None):
        """检查用户是否拥有指定权限码（UserRole→RolePermission→Permission 链路）

        Args:
            UserUUID: 用户 UUID
            PermCode: 权限码（如 'Order:update'）
            CompanyUUID: 可选，限定公司范围（默认按用户角色关联的公司集合过滤，
                与 core.User.HasPermission 原逻辑一致）

        Returns:
            bool
        """
        if not PermCode or not UserUUID:
            return False
        try:
            user_bytes = UUIDObj(UserUUID).bytes
        except Exception:
            user_bytes = UserUUID
        perm_uuids = set(
            Permission.objects.filter(PermCode=PermCode, PermStatus=1)
            .values_list('PermUUID', flat=True)
        )
        if not perm_uuids:
            return False
        user_roles = UserRole.objects.filter(UserUUID=user_bytes)
        user_role_uuids = user_roles.values_list('RoleUUID', flat=True)
        if not user_role_uuids:
            return False
        rp_qs = RolePermission.objects.filter(
            RoleUUID__in=user_role_uuids,
            PermUUID__in=perm_uuids,
        )
        if CompanyUUID is not None:
            rp_qs = rp_qs.filter(CompanyUUID=CompanyUUID)
        else:
            # 默认按用户角色关联的公司集合过滤（与 core.User.HasPermission 一致）
            company_uuids = list(set(user_roles.values_list('CompanyUUID', flat=True)))
            if company_uuids:
                rp_qs = rp_qs.filter(CompanyUUID__in=company_uuids)
        return rp_qs.exists()

    @staticmethod
    def CheckTablePermission(RoleUUID, PermCode, CompanyUUID=None, TableName=None):
        """检查角色对某表某动作权限码的 RBAC 判定

        组合判定：权限码是否定义 + 角色是否拥有 + 角色是否参与该表授权。

        Args:
            RoleUUID: 角色 UUID（可为 None，视为未参与授权）
            PermCode: 完整权限码（如 'Order:update'）
            CompanyUUID: 可选，公司 UUID
            TableName: 表名（用于"参与授权"回退判定，如 'Order'）

        Returns:
            (Granted, Decided) 元组：
            - (True, True): 已授权，放行
            - (False, True): 明确拒绝（角色参与该表授权但缺此动作权限）
            - (None, False): 权限码未定义或角色未参与该表授权，回退调用方原有逻辑
        """
        # 1. 权限码是否在系统中定义
        perm_qs = Permission.objects.filter(PermCode=PermCode, PermStatus=1)
        if not perm_qs.exists():
            return None, False
        perm_uuids = set(perm_qs.values_list('PermUUID', flat=True))
        if not RoleUUID:
            return None, False
        # 2. 角色是否拥有该权限
        rp_qs = RolePermission.objects.filter(RoleUUID=RoleUUID, PermUUID__in=perm_uuids)
        if CompanyUUID is not None:
            rp_qs = rp_qs.filter(CompanyUUID=CompanyUUID)
        if rp_qs.exists():
            return True, True
        # 3. 角色是否参与该表授权（拥有该表的其他权限码）
        if not TableName:
            return False, True
        role_perm_uuids = set(
            RolePermission.objects.filter(RoleUUID=RoleUUID)
            .values_list('PermUUID', flat=True)
        )
        if not role_perm_uuids:
            return None, False
        has_table_perm = Permission.objects.filter(
            PermUUID__in=role_perm_uuids,
            PermCode__startswith=f'{TableName}:',
            PermStatus=1,
        ).exists()
        if not has_table_perm:
            return None, False
        return False, True

    @staticmethod
    def EnsureDefaultRoleForUser(UserUUID, CompanyUUID, AdminUserUUID=None):
        """为新用户关联全局默认角色（"默认角色"在系统租户下唯一）

        租户解析顺序：系统公司所属租户 → 操作管理员角色租户 → 系统首个租户。

        Args:
            UserUUID: 新用户 UUID（bytes/str/UUIDObj 均可）
            CompanyUUID: 公司 UUID（通常为系统公司）
            AdminUserUUID: 可选，操作管理员 UUID（租户回退用）

        Returns:
            (UserRole 实例 or None, 错误消息 or None)
        """
        company_bytes = UUIDObj(CompanyUUID).bytes
        # 角色租户优先取自系统公司所属租户
        tenant_uuid = None
        company = Company.objects.filter(CompanyUUID=company_bytes).first()
        if company is not None:
            tenant_uuid = company.TenantUUID
        # 回退：当前操作管理员的首个角色租户
        if not tenant_uuid and AdminUserUUID:
            try:
                admin_ur = UserRole.objects.filter(
                    UserUUID=UUIDObj(AdminUserUUID).bytes,
                ).first()
                if admin_ur and admin_ur.RoleUUID:
                    role = Role.objects.filter(RoleUUID=admin_ur.RoleUUID).first()
                    tenant_uuid = role.TenantUUID if role else None
            except Exception:
                tenant_uuid = None
        # 回退：系统首个租户
        if not tenant_uuid:
            tenant = Tenant.objects.first()
            tenant_uuid = tenant.TenantUUID if tenant else None
        if not tenant_uuid:
            return None, '无法确定租户'

        # 查找或创建全局唯一的"默认角色"
        default_role = Role.objects.filter(
            TenantUUID=tenant_uuid,
            RoleName='默认角色',
        ).first()
        if not default_role:
            default_role = Role.objects.create(
                RoleUUID=uuid.uuid4().bytes,
                TenantUUID=tenant_uuid,
                RoleName='默认角色',
                RoleStatus=1,
                AssignScope=0,
                RoleDesc='系统默认角色，所有新用户自动关联',
                CreateTime=_timezone.now(),
            )

        # 避免重复关联
        if RoleManager.HasRole(UserUUID, default_role.RoleUUID, CompanyUUID):
            return None, None
        ur = UserRole.objects.create(
            UserRoleUUID=uuid.uuid4().bytes,
            UserUUID=UUIDObj(UserUUID).bytes,
            RoleUUID=default_role.RoleUUID,
            CompanyUUID=company_bytes,
        )
        return ur, None

    @staticmethod
    def EnsureTenantAdminRole(TenantUUID):
        """为租户（集团）创建/复用"集团超级管理员"角色并分配 admin:tenant 权限

        Args:
            TenantUUID: 租户 UUID（bytes/str/UUIDObj 均可）

        Returns:
            Role 实例（已创建或已存在）
        """
        tenant_bytes = UUIDObj(TenantUUID).bytes
        role = Role.objects.filter(
            TenantUUID=tenant_bytes,
            RoleName='集团超级管理员',
        ).first()
        if role:
            return role

        role = RoleManager.CreateRole(
            '集团超级管理员', tenant_bytes,
            AssignScope=0,
            RoleDesc='集团超级管理员，管理本租户下所有事务',
        )

        # 分配 admin:tenant 权限（租户内管理员：关闭公司隔离，保留租户隔离）
        admin_perm = RoleManager.GetPermissionByCode('admin:tenant')
        if admin_perm:
            RoleManager.AssignPermission(
                role.RoleUUID, admin_perm.PermUUID, _SYSTEM_COMPANY_UUID,
            )
        return role

    @staticmethod
    def GetUserCompanyUUIDs(UserUUID):
        """获取用户关联的公司 UUID 列表（经 UserRole，去重）

        Args:
            UserUUID: 用户 UUID（bytes/str/UUIDObj 均可）

        Returns:
            list[bytes]：CompanyUUID 列表（去重，保持首次出现顺序）
        """
        rows = UserRole.objects.filter(
            UserUUID=UUIDObj(UserUUID).bytes
        ).values_list('CompanyUUID', flat=True)
        result = []
        for c in rows:
            if c and c not in result:
                result.append(c)
        return result

    @staticmethod
    def ListUserRoleUUIDs(UserUUID, CompanyUUID=None):
        """获取用户的"用户-角色"关联 UUID 列表（UserRoleUUID）

        Args:
            UserUUID: 用户 UUID（bytes/str/UUIDObj 均可）
            CompanyUUID: 可选，限定公司范围

        Returns:
            list[bytes]：UserRoleUUID 列表
        """
        qs = UserRole.objects.filter(UserUUID=UUIDObj(UserUUID).bytes)
        if CompanyUUID is not None:
            qs = qs.filter(CompanyUUID=UUIDObj(CompanyUUID).bytes)
        return list(qs.values_list('UserRoleUUID', flat=True))

    @staticmethod
    def GetRolesForTenant(TenantUUID, RoleStatus=None):
        """获取租户下的角色列表（按角色名排序）

        Args:
            TenantUUID: 租户 UUID（bytes/str/UUIDObj 均可）
            RoleStatus: 可选，角色状态过滤（如 1=启用）

        Returns:
            QuerySet[Role]
        """
        qs = Role.objects.filter(TenantUUID=UUIDObj(TenantUUID).bytes)
        if RoleStatus is not None:
            qs = qs.filter(RoleStatus=RoleStatus)
        return qs.order_by('RoleName')

    @staticmethod
    def GetRoleNameMap(RoleUUIDs):
        """批量获取 RoleUUID(bytes) → RoleName 映射（仅含存在的角色）

        Args:
            RoleUUIDs: UUID 集合（bytes/str/UUIDObj 混合均可）

        Returns:
            dict[bytes, str]：键为 UUID 的 16 字节形式，与调用方字节键对齐
        """
        uuids = [UUIDObj(u).bytes for u in (RoleUUIDs or []) if u]
        if not uuids:
            return {}
        result = {}
        for r in Role.objects.filter(RoleUUID__in=uuids):
            try:
                result[r.RoleUUID.bytes] = r.RoleName
            except AttributeError:
                result[UUIDObj(r.RoleUUID).bytes] = r.RoleName
        return result

    @staticmethod
    def GetUserRoleDepartmentsForUserRoles(UserRoleUUIDs, CompanyUUID=None):
        """批量获取多个"用户-角色"关联的部门归属行

        Args:
            UserRoleUUIDs: UserRoleUUID 集合
            CompanyUUID: 可选，限定公司范围

        Returns:
            list[UserRoleDepartment]
        """
        uuids = [UUIDObj(u).bytes for u in (UserRoleUUIDs or []) if u]
        if not uuids:
            return []
        qs = UserRoleDepartment.objects.filter(UserRoleUUID__in=uuids)
        if CompanyUUID is not None:
            qs = qs.filter(CompanyUUID=UUIDObj(CompanyUUID).bytes)
        return list(qs)

    @staticmethod
    def GetPermissionByUUID(PermUUID):
        """按 UUID 查询权限（不存在返回 None）"""
        if not PermUUID:
            return None
        return Permission.objects.filter(PermUUID=UUIDObj(PermUUID).bytes).first()

    @staticmethod
    def GetRolePermission(RoleUUID, PermUUID, CompanyUUID=None, MatchNull=False):
        """查询角色-权限关联行（不存在返回 None）

        Args:
            RoleUUID: 角色 UUID
            PermUUID: 权限 UUID
            CompanyUUID: 可选，限定公司范围
            MatchNull: CompanyUUID 为 None 时是否视为 ``IS NULL`` 精确匹配
                （默认 False = 不做公司过滤；True 用于"公司为空"的唯一性校验）

        Returns:
            RolePermission 实例或 None
        """
        qs = RolePermission.objects.filter(
            RoleUUID=UUIDObj(RoleUUID).bytes,
            PermUUID=UUIDObj(PermUUID).bytes,
        )
        if MatchNull:
            qs = qs.filter(
                CompanyUUID=UUIDObj(CompanyUUID).bytes if CompanyUUID is not None else None
            )
        elif CompanyUUID is not None:
            qs = qs.filter(CompanyUUID=UUIDObj(CompanyUUID).bytes)
        return qs.first()

    @staticmethod
    def GetRolePermissionCompanies(RoleUUID, PermCode):
        """获取角色在指定权限码下被授权的公司 UUID 列表

        链路：Permission(PermCode) → RolePermission(RoleUUID) 的 CompanyUUID，
        用于 admin:company 范围解析。空 UUID（b'\\x00'*16）被过滤。

        Args:
            RoleUUID: 角色 UUID
            PermCode: 权限键（如 'admin:company'）

        Returns:
            list[bytes]：去重前的公司 UUID 列表
        """
        perm_uuids = list(Permission.objects.filter(
            PermCode=PermCode, PermStatus=1,
        ).values_list('PermUUID', flat=True))
        if not perm_uuids:
            return []
        companies = []
        for rp in RolePermission.objects.filter(
                RoleUUID=UUIDObj(RoleUUID).bytes, PermUUID__in=perm_uuids):
            if rp.CompanyUUID and rp.CompanyUUID != b'\x00' * 16:
                companies.append(UUIDObj(rp.CompanyUUID).bytes)
        return companies

    @staticmethod
    def RemoveUserRoleRelations(UserRoleUUID):
        """删除"用户-角色"关联下的部门/工作组归属（含职务子表），防孤儿数据

        与 gvsds FormService.delete_record 的 UserRole 级联清理零行为差异：
        先清空各 UserRoleDepartment 的职务子表，再删 URD / URW 行。

        Args:
            UserRoleUUID: UserRoleUUID（bytes/str/UUIDObj 均可）

        Returns:
            dict：{'Departments': n, 'Workgroups': m, 'Positions': k}
        """
        if not UserRoleUUID:
            return {'Departments': 0, 'Workgroups': 0, 'Positions': 0}
        ur_bytes = UUIDObj(UserRoleUUID).bytes
        position_count = 0
        urds = list(UserRoleDepartment.objects.filter(UserRoleUUID=ur_bytes))
        for urd in urds:
            position_count += PositionManager.ClearUrdPositions(urd.UserRoleDepartmentUUID) or 0
        dept_deleted, _ = UserRoleDepartment.objects.filter(UserRoleUUID=ur_bytes).delete()
        wg_deleted, _ = UserRoleWorkgroup.objects.filter(UserRoleUUID=ur_bytes).delete()
        return {
            'Departments': dept_deleted,
            'Workgroups': wg_deleted,
            'Positions': position_count,
        }


class PositionManager:
    """职务管理器——提供人事职务标签创建、岗位模板管理"""

    # ── 人事职务标签 ──

    @staticmethod
    def CreatePosition(Name, TenantUUID, Code='', Level=0, **Kwargs):
        """创建人事职务标签（TenantUUID 传 UUIDObj/str/bytes）

        Kwargs:
            PositionDesc: 职务描述
            CategoryUUID: 可选，挂靠的通用部门（PositionCategory）
        """
        position = Position(
            PositionUUID=uuid.uuid4().bytes,
            TenantUUID=TenantUUID,
            PositionName=Name,
            PositionCode=Code,
            PositionLevel=Level,
            PositionStatus=1,
            PositionDesc=Kwargs.get('PositionDesc', ''),
            CategoryUUID=Kwargs.get('CategoryUUID'),
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
    def ListPositions(TenantUUID, Names=None, CategoryUUID=None, OrderByName=False):
        """列出租户下的职务（不过滤状态；可按名称集合 / 通用部门过滤）

        Args:
            TenantUUID: 租户 UUID（bytes/str/UUIDObj 均可）
            Names: 可选，职务名称集合（PositionName__in）
            CategoryUUID: 可选，限定通用部门
            OrderByName: True 时按 PositionName 排序

        Returns:
            QuerySet[Position]
        """
        tenant_bytes = PositionManager._ToBytes(TenantUUID)
        if tenant_bytes is None:
            return Position.objects.none()
        qs = Position.objects.filter(TenantUUID=tenant_bytes)
        if Names:
            qs = qs.filter(PositionName__in=list(Names))
        if CategoryUUID:
            cat_bytes = PositionManager._ToBytes(CategoryUUID)
            if cat_bytes is not None:
                qs = qs.filter(CategoryUUID=cat_bytes)
        if OrderByName:
            qs = qs.order_by('PositionName')
        return qs

    @staticmethod
    def ListPositionCodes(TenantUUID, CategoryUUID=None, Prefix=''):
        """获取职务编码列表（可按通用部门 / 编码前缀过滤）

        供职务编码自动编号使用：如通用部门编码 FIN → 已有 FIN01/FIN02，
        调用方取回编码列表后计算下一序号。

        Args:
            TenantUUID: 租户 UUID（bytes/str/UUIDObj 均可）
            CategoryUUID: 可选，限定通用部门（PositionCategory）
            Prefix: 可选，编码前缀过滤（PositionCode__startswith）

        Returns:
            list[str]：职务编码列表（可能含空值）
        """
        tenant_bytes = PositionManager._ToBytes(TenantUUID)
        if tenant_bytes is None:
            return []
        qs = Position.objects.filter(TenantUUID=tenant_bytes)
        if CategoryUUID:
            cat_bytes = PositionManager._ToBytes(CategoryUUID)
            if cat_bytes is not None:
                qs = qs.filter(CategoryUUID=cat_bytes)
        if Prefix:
            qs = qs.filter(PositionCode__startswith=Prefix)
        return list(qs.values_list('PositionCode', flat=True))

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
        allowed = ('PositionName', 'PositionCode', 'PositionLevel', 'PositionStatus', 'PositionDesc', 'CategoryUUID')
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

    @staticmethod
    def PurgePosition(PositionUUID):
        """物理删除职务（供应用模板移除等场景）；返回是否命中

        注意：与 DeletePosition（软删除）语义不同，仅当确认无员工持有时调用。
        """
        position = Position.objects.filter(PositionUUID=PositionUUID).first()
        if not position:
            return False
        position.delete()
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
    def GetPermInTemplate(TemplateUUID, PermUUID):
        """查询岗位模板中的权限关联（不存在返回 None）"""
        return PositionTemplatePermission.objects.filter(
            PositionTemplateUUID=TemplateUUID,
            PermUUID=PermUUID,
        ).first()

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

    # ── 通用部门（职务分类目录） ──────────────────────────────

    @staticmethod
    def CreateCategory(Name, TenantUUID, Code, Sort=0, Status=1, Desc=''):
        """创建通用部门（职务分类目录）；返回 PositionCategory 实例"""
        cat = PositionCategory(
            PositionCategoryUUID=uuid.uuid4().bytes,
            TenantUUID=TenantUUID,
            CategoryName=Name,
            CategoryCode=Code,
            CategorySort=Sort,
            CategoryStatus=Status,
            CategoryDesc=Desc,
            CreateTime=_timezone.now(),
            UpdateTime=_timezone.now(),
        )
        cat.save()
        return cat

    @staticmethod
    def GetCategoriesForTenant(TenantUUID, Names=None):
        """获取租户下全部通用部门（含禁用，用于目录展示/搜索）

        Args:
            TenantUUID: 租户 UUID（bytes/str/UUIDObj 均可）
            Names: 可选，部门名称集合（CategoryName__in）

        Returns:
            QuerySet[PositionCategory]
        """
        tenant_bytes = PositionManager._ToBytes(TenantUUID)
        if tenant_bytes is None:
            return PositionCategory.objects.none()
        qs = PositionCategory.objects.filter(TenantUUID=tenant_bytes)
        if Names:
            qs = qs.filter(CategoryName__in=list(Names))
        return qs

    @staticmethod
    def GetCategoryByUUID(CategoryUUID):
        """按 UUID 查询通用部门（不存在返回 None）"""
        return PositionCategory.objects.filter(PositionCategoryUUID=CategoryUUID).first()

    @staticmethod
    def UpdateCategory(CategoryUUID, **Fields):
        """更新通用部门字段（白名单：CategoryName/CategoryCode/CategorySort/CategoryStatus/CategoryDesc）

        Returns:
            更新后的 PositionCategory 实例；不存在返回 None
        """
        cat = PositionCategory.objects.filter(PositionCategoryUUID=CategoryUUID).first()
        if not cat:
            return None
        allowed = ('CategoryName', 'CategoryCode', 'CategorySort', 'CategoryStatus', 'CategoryDesc')
        update_fields = []
        for key, value in Fields.items():
            if key in allowed:
                setattr(cat, key, value)
                update_fields.append(key)
        cat.UpdateTime = _timezone.now()
        update_fields.append('UpdateTime')
        if update_fields:
            cat.save(update_fields=update_fields)
        return cat

    @staticmethod
    def DeleteCategory(CategoryUUID):
        """软删除通用部门（CategoryStatus=0）；返回是否命中"""
        cat = PositionCategory.objects.filter(PositionCategoryUUID=CategoryUUID).first()
        if not cat:
            return False
        cat.CategoryStatus = 0
        cat.UpdateTime = _timezone.now()
        cat.save(update_fields=['CategoryStatus', 'UpdateTime'])
        return True

    @staticmethod
    def PurgeCategory(CategoryUUID):
        """物理删除通用部门（供应用模板移除等场景）；返回是否命中

        注意：与 DeleteCategory（软删除）语义不同，仅当确认其下已无职务时调用。
        """
        cat = PositionCategory.objects.filter(PositionCategoryUUID=CategoryUUID).first()
        if not cat:
            return False
        cat.delete()
        return True

    # ── 部门职务多值子表（UserRoleDepartmentPosition） ────────

    @staticmethod
    def _ToBytes(value):
        """把 bytes/str/UUIDObj 归一化为 bytes；无效返回 None"""
        if value is None:
            return None
        try:
            return UUIDObj(value).bytes
        except Exception:
            return None

    @staticmethod
    def GetUrdPositionRows(UserRoleDepartmentUUID):
        """查询 URD 关联的全部职务子表行（UserRoleDepartmentPosition）"""
        return UserRoleDepartmentPosition.objects.filter(UserRoleDepartmentUUID=UserRoleDepartmentUUID)

    @staticmethod
    def GetUrdPositionUUIDs(UserRoleDepartmentUUID):
        """URD 关联的职务 UUID 列表（UUIDObj 元素）"""
        return list(
            UserRoleDepartmentPosition.objects.filter(
                UserRoleDepartmentUUID=UserRoleDepartmentUUID
            ).values_list('PositionUUID', flat=True)
        )

    @staticmethod
    def GetUrdPositionRowsForUrds(URDUUIDs):
        """批量取多个 URD 的职务子表行（无效项自动跳过）

        Args:
            URDUUIDs: UserRoleDepartmentUUID 集合（bytes/str/UUIDObj 均可）

        Returns:
            list[UserRoleDepartmentPosition]
        """
        urd_uuids = []
        for u in (URDUUIDs or []):
            if not u:
                continue
            try:
                urd_uuids.append(UUIDObj(u).bytes)
            except Exception:
                continue
        if not urd_uuids:
            return []
        return list(
            UserRoleDepartmentPosition.objects.filter(UserRoleDepartmentUUID__in=urd_uuids)
        )

    @staticmethod
    def GetPositionNameMap(PositionUUIDs):
        """批量获取 PositionUUID(bytes) → PositionName 映射（仅含存在的职务）

        Args:
            PositionUUIDs: UUID 集合（bytes/str/UUIDObj 混合均可）

        Returns:
            dict[bytes, str]：键为 UUID 的 16 字节形式，与调用方字节键对齐
        """
        uuids = []
        for u in (PositionUUIDs or []):
            if not u:
                continue
            try:
                uuids.append(UUIDObj(u).bytes)
            except Exception:
                continue
        if not uuids:
            return {}
        result = {}
        for p in Position.objects.filter(PositionUUID__in=uuids):
            try:
                result[p.PositionUUID.bytes] = p.PositionName
            except AttributeError:
                result[UUIDObj(p.PositionUUID).bytes] = p.PositionName
        return result

    @staticmethod
    def GetHeldPositionUUIDs():
        """所有员工当前持有的职务 UUID bytes 集合（UserRoleDepartmentPosition 引用）

        用于职务模板移除前的占用校验（被持有的职务不可删除）。
        """
        return {
            PositionManager._ToBytes(r.PositionUUID)
            for r in UserRoleDepartmentPosition.objects.all()
            if r.PositionUUID
        }

    @staticmethod
    def HasUrdPosition(UserRoleDepartmentUUID, PositionUUID):
        """URD 是否已挂该职务（bytes 归一化比较）"""
        urd_pk = PositionManager._ToBytes(UserRoleDepartmentUUID)
        pos_pk = PositionManager._ToBytes(PositionUUID)
        if urd_pk is None or pos_pk is None:
            return False
        return UserRoleDepartmentPosition.objects.filter(
            UserRoleDepartmentUUID=urd_pk, PositionUUID=pos_pk
        ).exists()

    @staticmethod
    def AddPositionToUrd(UserRoleDepartmentUUID, PositionUUID, CompanyUUID=None, TenantUUID=None):
        """给 URD 挂职务（重复/并发冲突跳过返回 None）；成功返回子表行"""
        urd_pk = PositionManager._ToBytes(UserRoleDepartmentUUID)
        pos_pk = PositionManager._ToBytes(PositionUUID)
        if urd_pk is None or pos_pk is None:
            return None
        if PositionManager.HasUrdPosition(urd_pk, pos_pk):
            return None
        try:
            return UserRoleDepartmentPosition.objects.create(
                UserRoleDepartmentPositionUUID=uuid.uuid4().bytes,
                UserRoleDepartmentUUID=urd_pk,
                PositionUUID=pos_pk,
                TenantUUID=TenantUUID,
                CompanyUUID=CompanyUUID,
            )
        except Exception:
            return None

    @staticmethod
    def RemovePositionFromUrd(UserRoleDepartmentUUID, PositionUUID):
        """移除 URD 上的职务；返回是否命中"""
        urd_pk = PositionManager._ToBytes(UserRoleDepartmentUUID)
        pos_pk = PositionManager._ToBytes(PositionUUID)
        if urd_pk is None or pos_pk is None:
            return False
        deleted, _ = UserRoleDepartmentPosition.objects.filter(
            UserRoleDepartmentUUID=urd_pk, PositionUUID=pos_pk
        ).delete()
        return deleted > 0

    @staticmethod
    def ClearUrdPositions(UserRoleDepartmentUUID):
        """清空 URD 上的全部职务（级联删除用）；返回删除条数"""
        urd_pk = PositionManager._ToBytes(UserRoleDepartmentUUID)
        if urd_pk is None:
            return 0
        deleted, _ = UserRoleDepartmentPosition.objects.filter(
            UserRoleDepartmentUUID=urd_pk
        ).delete()
        return deleted

    @staticmethod
    def SetUrdPositions(UserRoleDepartmentUUID, PositionUUIDs, CompanyUUID=None, TenantUUID=None):
        """整体重写 URD 职务集合；返回 (created, removed)"""
        urd_pk = PositionManager._ToBytes(UserRoleDepartmentUUID)
        if urd_pk is None:
            return (0, 0)
        removed, _ = UserRoleDepartmentPosition.objects.filter(
            UserRoleDepartmentUUID=urd_pk
        ).delete()
        created = 0
        for _pos in PositionUUIDs or []:
            if PositionManager.AddPositionToUrd(urd_pk, _pos, CompanyUUID, TenantUUID):
                created += 1
        return (created, removed)

    @staticmethod
    def ResolvePrimaryUrd(UserRoleUUID, CompanyUUID=None):
        """解析 UserRoleUUID → 主 UserRoleDepartment 关联（同公司优先，否则首条）；无则 None"""
        ur_bytes = PositionManager._ToBytes(UserRoleUUID)
        if ur_bytes is None:
            return None
        qs = UserRoleDepartment.objects.filter(UserRoleUUID=ur_bytes)
        company_bytes = PositionManager._ToBytes(CompanyUUID)
        if company_bytes:
            qs = qs.filter(CompanyUUID=company_bytes)
        return qs.first()

    @staticmethod
    def WritePositionsToPrimaryUrd(UserRoleUUID, PositionUUIDs, CompanyUUID=None, TenantUUID=None, Rewrite=True):
        """把职务集合写入 UserRole 的主部门关联（URDP 子表）。

        Rewrite=True 整体重写（编辑回显全量，对齐 UserRole 表编辑职务语义）；
        Rewrite=False 仅追加缺失（新增成员时保留该员工在部门已有的职务）。
        返回实际写入数量；无主关联时返回 0。
        """
        urd = PositionManager.ResolvePrimaryUrd(UserRoleUUID, CompanyUUID)
        if urd is None:
            return 0
        if Rewrite:
            UserRoleDepartmentPosition.objects.filter(
                UserRoleDepartmentUUID=urd.UserRoleDepartmentUUID
            ).delete()
        created = 0
        for _pos in PositionUUIDs or []:
            if PositionManager.AddPositionToUrd(
                urd.UserRoleDepartmentUUID,
                _pos,
                CompanyUUID=getattr(urd, 'CompanyUUID', None),
                TenantUUID=getattr(urd, 'TenantUUID', None),
            ):
                created += 1
        return created

    @staticmethod
    def BatchAddPositions(urd_objs, position_bytes):
        """批量给多个 URD 关联追加职务（bytes 归一化查重防联合唯一 1062，重复跳过）。

        urd_objs: UserRoleDepartment 实例列表（取 UserRoleDepartmentUUID/TenantUUID/CompanyUUID）。
        position_bytes: bytes 职务 UUID 列表。返回实际新增数。
        """
        affected = 0
        if not urd_objs or not position_bytes:
            return 0
        urd_pks = []
        for _u in urd_objs:
            _pk = PositionManager._ToBytes(getattr(_u, 'UserRoleDepartmentUUID', None))
            if _pk:
                urd_pks.append(_pk)
        existing = set()
        for _r in UserRoleDepartmentPosition.objects.filter(
            UserRoleDepartmentUUID__in=urd_pks, PositionUUID__in=position_bytes
        ).values_list('UserRoleDepartmentUUID', 'PositionUUID'):
            existing.add((PositionManager._ToBytes(_r[0]), PositionManager._ToBytes(_r[1])))
        for _u in urd_objs:
            _urd_pk = PositionManager._ToBytes(getattr(_u, 'UserRoleDepartmentUUID', None))
            if _urd_pk is None:
                continue
            for _pb in position_bytes:
                if (_urd_pk, _pb) in existing:
                    continue
                try:
                    UserRoleDepartmentPosition.objects.create(
                        UserRoleDepartmentPositionUUID=uuid.uuid4().bytes,
                        UserRoleDepartmentUUID=_u.UserRoleDepartmentUUID,
                        PositionUUID=_pb,
                        TenantUUID=getattr(_u, 'TenantUUID', None),
                        CompanyUUID=getattr(_u, 'CompanyUUID', None),
                    )
                    affected += 1
                except Exception:
                    continue
        return affected

    @staticmethod
    def BatchRemovePositions(urd_objs, position_bytes):
        """批量移除多个 URD 关联上的职务；返回移除条数"""
        if not urd_objs or not position_bytes:
            return 0
        urd_pks = []
        for _u in urd_objs:
            _pk = PositionManager._ToBytes(getattr(_u, 'UserRoleDepartmentUUID', None))
            if _pk:
                urd_pks.append(_pk)
        _del_qs = UserRoleDepartmentPosition.objects.filter(
            UserRoleDepartmentUUID__in=urd_pks, PositionUUID__in=position_bytes
        )
        affected = _del_qs.count()
        _del_qs.delete()
        return affected

    # ── 职务搜索（列表 + 通用部门树） ────────────────────────

    @staticmethod
    def SearchPositions(TenantUUID, Keyword='', Tree=False, Limit=50, PriorityCategoryUUIDs=None):
        """搜索租户职务：list 平铺 + tree 通用部门树（全部租户分类，PriorityCategoryUUIDs 排前）。

        Args:
            TenantUUID: 租户 UUID（bytes/str/UUIDObj）；为空返回空列表（fail-closed）
            Keyword: 职务名称关键字
            Tree: True 时返回通用部门树 + 职务归组（不截断）；False 平铺且限 Limit 条
            Limit: 非 tree 模式条数上限
            PriorityCategoryUUIDs: 排序置前的通用部门 UUID 集合（bytes/str 均可）

        Returns:
            {'list': [{PositionUUID(str), PositionName, PositionCode, CategoryUUID(str)}],
             'tree': [{uuid, name, parentUuid}]}（tree 模式才有）
        """
        tenant_bytes = PositionManager._ToBytes(TenantUUID)
        if tenant_bytes is None:
            return {'list': [], 'tree': []}
        qs = Position.objects.filter(TenantUUID=tenant_bytes)
        if Keyword:
            qs = qs.filter(PositionName__icontains=Keyword)
        if not Tree:
            qs = qs[:Limit]
        data = list(qs.values('PositionUUID', 'PositionName', 'PositionCode', 'CategoryUUID'))
        for item in data:
            if item.get('PositionUUID'):
                item['PositionUUID'] = str(UUIDObj(item['PositionUUID']))
            if item.get('CategoryUUID'):
                item['CategoryUUID'] = str(UUIDObj(item['CategoryUUID']))
        result = {'list': data}
        if Tree:
            cat_rows = list(PositionCategory.objects.filter(TenantUUID=tenant_bytes).values(
                'PositionCategoryUUID', 'CategoryName'
            ))
            if PriorityCategoryUUIDs:
                pri_cats = set()
                for _c in PriorityCategoryUUIDs:
                    _pb = PositionManager._ToBytes(_c)
                    if _pb:
                        pri_cats.add(_pb)

                def _cat_key(c):
                    try:
                        _pb = UUIDObj(c['PositionCategoryUUID']).bytes
                    except Exception:
                        _pb = None
                    return (0 if _pb in pri_cats else 1, c['CategoryName'] or '')

                cat_rows.sort(key=_cat_key)
            result['tree'] = [
                {
                    'uuid': str(UUIDObj(c['PositionCategoryUUID'])),
                    'name': c['CategoryName'],
                    'parentUuid': '',
                }
                for c in cat_rows
            ]
        return result

    @staticmethod
    def SearchCategories(TenantUUID, Keyword='', Limit=20):
        """搜索租户通用部门（职务分类目录）；返回 [{PositionCategoryUUID(str), CategoryName}]"""
        tenant_bytes = PositionManager._ToBytes(TenantUUID)
        if tenant_bytes is None:
            return []
        qs = PositionCategory.objects.filter(TenantUUID=tenant_bytes)
        if Keyword:
            qs = qs.filter(CategoryName__icontains=Keyword)
        data = list(qs[:Limit].values('PositionCategoryUUID', 'CategoryName'))
        for item in data:
            if item.get('PositionCategoryUUID'):
                item['PositionCategoryUUID'] = str(UUIDObj(item['PositionCategoryUUID']))
        return data

    # ── 应用虚拟职务绑定 ─────────────────────────────────────

    @staticmethod
    def GetVirtualBindings(AppUUID, CompanyUUID=None):
        """查询应用的虚拟职务绑定（含实际职务名回显）

        Returns:
            [{'AppVirtualBindingUUID'(str), 'VirtualPositionUUID'(str), 'VirtualPositionName',
              'PositionUUID'(str), 'PositionName', 'CompanyUUID'(str)}]
        """
        app_bytes = PositionManager._ToBytes(AppUUID)
        if app_bytes is None:
            return []
        qs = AppVirtualBinding.objects.filter(AppUUID=app_bytes)
        company_bytes = PositionManager._ToBytes(CompanyUUID)
        if company_bytes:
            qs = qs.filter(CompanyUUID=company_bytes)
        bindings = list(qs)
        pos_map = {PositionManager._ToBytes(p.PositionUUID): p.PositionName for p in Position.objects.filter(
            PositionUUID__in=[b.PositionUUID for b in bindings] if bindings else []
        )}
        vp_map = {PositionManager._ToBytes(v.AppVirtualPositionUUID): v.Name for v in AppVirtualPosition.objects.filter(
            AppUUID=app_bytes
        )}
        result = []
        for b in bindings:
            pos_pb = PositionManager._ToBytes(b.PositionUUID)
            vp_pb = PositionManager._ToBytes(b.VirtualPositionUUID)
            result.append({
                'AppVirtualBindingUUID': str(UUIDObj(b.AppVirtualBindingUUID)) if b.AppVirtualBindingUUID else '',
                'VirtualPositionUUID': str(UUIDObj(b.VirtualPositionUUID)) if b.VirtualPositionUUID else '',
                'VirtualPositionName': vp_map.get(vp_pb, ''),
                'PositionUUID': str(UUIDObj(b.PositionUUID)) if b.PositionUUID else '',
                'PositionName': pos_map.get(pos_pb, ''),
                'CompanyUUID': str(UUIDObj(b.CompanyUUID)) if b.CompanyUUID else '',
            })
        return result

    @staticmethod
    def SetVirtualBindings(AppUUID, VirtualPositionUUID, PositionUUIDs, CompanyUUID=None, TenantUUID=None):
        """重写虚拟职务的绑定（仅该公司维度，其它公司绑定保留）

        Args:
            AppUUID / VirtualPositionUUID: bytes/str/UUIDObj
            PositionUUIDs: 实际职务 UUID 列表（bytes/str）
            CompanyUUID: 公司维度（可为 None=全局范围）
        Returns:
            {'Bound': 成功绑定数, 'Skipped': 跳过数（联合唯一冲突/无效）}
        """
        app_bytes = PositionManager._ToBytes(AppUUID)
        vp_pb = PositionManager._ToBytes(VirtualPositionUUID)
        company_bytes = PositionManager._ToBytes(CompanyUUID)
        if app_bytes is None or vp_pb is None:
            return {'Bound': 0, 'Skipped': 0}
        _del_qs = AppVirtualBinding.objects.filter(
            AppUUID=app_bytes, VirtualPositionUUID=vp_pb
        )
        if company_bytes:
            _del_qs = _del_qs.filter(CompanyUUID=company_bytes)
        _del_qs.delete()
        bound = 0
        skipped = 0
        for _pos in PositionUUIDs or []:
            pos_pb = PositionManager._ToBytes(_pos)
            if pos_pb is None:
                skipped += 1
                continue
            try:
                AppVirtualBinding.objects.create(
                    AppVirtualBindingUUID=uuid.uuid4().bytes,
                    AppUUID=app_bytes,
                    VirtualPositionUUID=vp_pb,
                    PositionUUID=pos_pb,
                    TenantUUID=TenantUUID,
                    CompanyUUID=company_bytes,
                )
                bound += 1
            except Exception:
                # 联合唯一 (VirtualPositionUUID, PositionUUID) 不含公司：同名职务已被其它公司绑定时跳过
                skipped += 1
        return {'Bound': bound, 'Skipped': skipped}

    @staticmethod
    def AutoBindVirtualPositions(AppUUID, TenantUUID, CompanyUUID=None):
        """一键绑定：按名称把虚拟职务自动匹配到真实职务（前提：通用部门与职务模板已同步）。

        对每个虚拟职务（AppVirtualPosition.Name），在同租户 Position 中查找 PositionName 相同者，
        按公司维度写入 AppVirtualBinding（仅重写该公司/范围的旧绑定，其它公司绑定保留）。

        Returns:
            (ok, msg, stats): stats={'Bound': [虚拟职务名], 'Unmatched': [虚拟职务名]}
        """
        app_bytes = PositionManager._ToBytes(AppUUID)
        tenant_bytes = PositionManager._ToBytes(TenantUUID)
        if app_bytes is None or tenant_bytes is None:
            return False, '无法确定应用或租户', None
        company_bytes = PositionManager._ToBytes(CompanyUUID)
        vps = list(AppVirtualPosition.objects.filter(AppUUID=app_bytes))
        if not vps:
            return False, '该应用未声明虚拟职务', None
        pos_by_name = {p.PositionName: p for p in Position.objects.filter(TenantUUID=tenant_bytes)}
        bound = []
        unmatched = []
        for vp in vps:
            pos = pos_by_name.get(vp.Name)
            if pos is None:
                unmatched.append(vp.Name)
                continue
            # 仅重写该公司/范围的旧绑定（其它公司绑定保留）
            _del_qs = AppVirtualBinding.objects.filter(VirtualPositionUUID=vp.AppVirtualPositionUUID)
            if company_bytes:
                _del_qs = _del_qs.filter(CompanyUUID=company_bytes)
            _del_qs.delete()
            try:
                AppVirtualBinding.objects.create(
                    AppVirtualBindingUUID=uuid.uuid4().bytes,
                    AppUUID=app_bytes,
                    VirtualPositionUUID=vp.AppVirtualPositionUUID,
                    PositionUUID=pos.PositionUUID,
                    TenantUUID=tenant_bytes,
                    CompanyUUID=company_bytes,
                )
                bound.append(vp.Name)
            except Exception:
                unmatched.append(f'{vp.Name}（同名职务已被其它公司绑定）')
        stats = {'Bound': bound, 'Unmatched': unmatched}
        msg = f'一键绑定完成：绑定 {len(bound)} 个虚拟职务'
        if unmatched:
            msg += f'；{len(unmatched)} 个未绑定：{"、".join(unmatched)}（请先同步职务模板或检查名称）'
        return True, msg, stats


    @staticmethod
    def ListVirtualPositions(AppUUID=None, TenantUUID=None, CompanyUUID=None):
        """应用虚拟职务列表（按 Code 排序；可按应用/租户/公司过滤）"""
        qs = AppVirtualPosition.objects.all()
        app_bytes = PositionManager._ToBytes(AppUUID)
        if app_bytes:
            qs = qs.filter(AppUUID=app_bytes)
        tenant_bytes = PositionManager._ToBytes(TenantUUID)
        if tenant_bytes:
            qs = qs.filter(TenantUUID=tenant_bytes)
        company_bytes = PositionManager._ToBytes(CompanyUUID)
        if company_bytes:
            qs = qs.filter(CompanyUUID=company_bytes)
        return list(qs.order_by('Code'))

    @staticmethod
    def UpsertVirtualPosition(AppUUID, Code, Name, MenuNames, TenantUUID=None, CompanyUUID=None):
        """按 (AppUUID, Code) upsert 应用虚拟职务（无记录则补主键新建），返回实例"""
        app_bytes = PositionManager._ToBytes(AppUUID)
        avp = AppVirtualPosition.objects.filter(AppUUID=app_bytes, Code=Code).first()
        if avp is None:
            return AppVirtualPosition.objects.create(
                AppVirtualPositionUUID=uuid.uuid4().bytes,
                AppUUID=app_bytes,
                Code=Code,
                Name=Name,
                MenuNames=MenuNames,
                TenantUUID=PositionManager._ToBytes(TenantUUID),
                CompanyUUID=PositionManager._ToBytes(CompanyUUID),
            )
        if avp.Name != Name or avp.MenuNames != MenuNames:
            avp.Name = Name
            avp.MenuNames = MenuNames
            avp.save(update_fields=['Name', 'MenuNames'])
        return avp

    @staticmethod
    def DeleteVirtualPositions(AppUUID, Codes):
        """按应用 + 虚拟职务编码集合删除虚拟职务行，返回删除条数"""
        app_bytes = PositionManager._ToBytes(AppUUID)
        codes = [c for c in (Codes or []) if c]
        if app_bytes is None or not codes:
            return 0
        return AppVirtualPosition.objects.filter(AppUUID=app_bytes, Code__in=codes).delete()[0]

    @staticmethod
    def RemoveVirtualBindings(VirtualPositionUUID=None, PositionUUID=None, AppUUID=None):
        """删除应用虚拟职务绑定（可按虚拟职务/实际职务/应用过滤），返回删除条数

        三个过滤条件均为空时不动作（防止误删全表）。
        """
        vp_bytes = PositionManager._ToBytes(VirtualPositionUUID)
        pos_bytes = PositionManager._ToBytes(PositionUUID)
        app_bytes = PositionManager._ToBytes(AppUUID)
        if vp_bytes is None and pos_bytes is None and app_bytes is None:
            return 0
        qs = AppVirtualBinding.objects.all()
        if vp_bytes:
            qs = qs.filter(VirtualPositionUUID=vp_bytes)
        if pos_bytes:
            qs = qs.filter(PositionUUID=pos_bytes)
        if app_bytes:
            qs = qs.filter(AppUUID=app_bytes)
        return qs.delete()[0]

    @staticmethod
    def GetAppBoundPositionUUIDs(AppUUID):
        """应用已绑定的实际职务 UUID bytes 集合（AppPosition ∪ AppVirtualBinding）"""
        app_bytes = PositionManager._ToBytes(AppUUID)
        if app_bytes is None:
            return set()
        bound = set(AppPosition.objects.filter(AppUUID=app_bytes).values_list('PositionUUID', flat=True))
        bound |= set(AppVirtualBinding.objects.filter(AppUUID=app_bytes).values_list('PositionUUID', flat=True))
        return bound

    @staticmethod
    def GetUserPositionUUIDs(UserUUID, RoleUUID=None, CompanyUUID=None):
        """用户当前持有的职务 UUID bytes 集合（UserRole → UserRoleDepartment → UserRoleDepartmentPosition）

        传 RoleUUID 时仅统计该角色下的 UserRole（严格按当前角色显示菜单）；
        传 CompanyUUID 时仅统计该公司的部门关联。
        """
        user_bytes = PositionManager._ToBytes(UserUUID)
        if user_bytes is None:
            return set()
        ur_qs = UserRole.objects.filter(UserUUID=user_bytes)
        role_bytes = PositionManager._ToBytes(RoleUUID)
        if role_bytes:
            ur_qs = ur_qs.filter(RoleUUID=role_bytes)
        ur_uuids = list(ur_qs.values_list('UserRoleUUID', flat=True))
        if not ur_uuids:
            return set()
        urd_qs = UserRoleDepartment.objects.filter(UserRoleUUID__in=ur_uuids)
        company_bytes = PositionManager._ToBytes(CompanyUUID)
        if company_bytes:
            urd_qs = urd_qs.filter(CompanyUUID=company_bytes)
        urd_uuids = list(urd_qs.values_list('UserRoleDepartmentUUID', flat=True))
        if not urd_uuids:
            return set()
        return set(
            UserRoleDepartmentPosition.objects.filter(UserRoleDepartmentUUID__in=urd_uuids)
            .values_list('PositionUUID', flat=True)
        )


class ShopManager:
    """商铺管理器——商铺实体与角色-商铺关联管理"""

    @staticmethod
    def CreateShop(Name, CompanyUUID, Code='', ShopType=0,
                   Address='', Contact='', Phone='', Desc=''):
        """创建商铺

        Args:
            Name: 商铺名称
            CompanyUUID: 所属公司 UUID（UUIDObj/str/bytes 均可）
            Code: 商铺编码
            ShopType: 商铺类型（0=默认）
            Address: 商铺地址
            Contact: 联系人
            Phone: 联系电话
            Desc: 描述

        Returns:
            Shop 实例
        """
        shop = Shop(
            ShopUUID=uuid.uuid4().bytes,
            CompanyUUID=CompanyUUID,
            ShopName=Name,
            ShopCode=Code,
            ShopType=ShopType,
            ShopStatus=1,
            ShopAddress=Address,
            ShopContact=Contact,
            ShopPhone=Phone,
            ShopDesc=Desc,
            CreateTime=_timezone.now(),
            UpdateTime=_timezone.now(),
        )
        shop.save()
        return shop

    @staticmethod
    def GetShopByUUID(ShopUUID):
        """按 UUID 查询商铺（不存在返回 None）"""
        return Shop.objects.filter(ShopUUID=ShopUUID).first()

    @staticmethod
    def SearchShops(Keyword='', CompanyUUID=None, Limit=20):
        """搜索商铺（按名称模糊匹配，可选公司过滤，仅启用）

        Args:
            Keyword: 商铺名称关键字
            CompanyUUID: 可选，限定公司范围
            Limit: 返回条数上限

        Returns:
            QuerySet[Shop]
        """
        qs = Shop.objects.filter(ShopStatus=1)
        if Keyword:
            qs = qs.filter(ShopName__icontains=Keyword)
        if CompanyUUID is not None:
            qs = qs.filter(CompanyUUID=CompanyUUID)
        return qs[:Limit]

    @staticmethod
    def GetShopsForRole(RoleUUID):
        """获取角色关联的所有商铺"""
        shop_uuids = RoleShop.objects.filter(
            RoleUUID=RoleUUID,
        ).values_list('ShopUUID', flat=True)
        return Shop.objects.filter(ShopUUID__in=shop_uuids)

    @staticmethod
    def GetMainShopForRole(RoleUUID):
        """获取角色的主商铺（IsMainShop=1；不存在返回 None）"""
        rs = RoleShop.objects.filter(RoleUUID=RoleUUID, IsMainShop=1).first()
        if not rs:
            return None
        return Shop.objects.filter(ShopUUID=rs.ShopUUID).first()

    @staticmethod
    def AssignRole(RoleUUID, ShopUUID, IsMainShop=0):
        """为角色关联商铺

        Args:
            RoleUUID: 角色 UUID
            ShopUUID: 商铺 UUID
            IsMainShop: 是否设为主商铺（1=是）

        Returns:
            RoleShop 实例
        """
        rs = RoleShop(
            RoleShopUUID=uuid.uuid4().bytes,
            RoleUUID=RoleUUID,
            ShopUUID=ShopUUID,
            IsMainShop=IsMainShop,
            CreateTime=_timezone.now(),
        )
        rs.save()
        return rs

    @staticmethod
    def RemoveRole(RoleUUID, ShopUUID):
        """移除角色与商铺的关联；返回是否命中"""
        deleted, _ = RoleShop.objects.filter(
            RoleUUID=RoleUUID, ShopUUID=ShopUUID,
        ).delete()
        return deleted > 0


class AuthManager:
    """用户认证管理器——账号查询、创建、密码、2FA 与头像"""

    @staticmethod
    def GetUserByUserName(UserName):
        """按用户名查询用户（不存在返回 None）"""
        if not UserName:
            return None
        User = _GetUserModel()
        return User.objects.filter(UserName=UserName).first()

    @staticmethod
    def GetUserByUUID(UserUUID):
        """按 UUID 查询用户（不存在返回 None）"""
        if not UserUUID:
            return None
        User = _GetUserModel()
        return User.objects.filter(UserUUID=UUIDObj(UserUUID).bytes).first()

    @staticmethod
    def GetUserNames(UserUUIDs):
        """批量取用户名：{UserUUID 字节: UserName}

        单次 __in 查询，供列表页一次性回填用户名，避免逐个查库产生 N+1。

        Args:
            UserUUIDs: UUID 可迭代对象（bytes/str/UUIDObj 均可，无效项自动跳过）

        Returns:
            dict[bytes, str]：仅含查到的用户；用户名为空时该键值为空串
        """
        names = {}
        uuids = []
        for value in UserUUIDs or []:
            if not value:
                continue
            try:
                uuids.append(UUIDObj(value).bytes)
            except Exception:
                continue
        if not uuids:
            return names
        User = _GetUserModel()
        for u in User.objects.filter(UserUUID__in=uuids):
            if u.UserUUID:
                names[UUIDObj(u.UserUUID).bytes] = u.UserName or ''
        return names

    @staticmethod
    def CountUsers(UserUUIDs=None):
        """统计用户数量（可按 UUID 集合过滤；空集合返回 0）"""
        User = _GetUserModel()
        qs = User.objects.all()
        if UserUUIDs is not None:
            qs = qs.filter(UserUUID__in=UserUUIDs) if UserUUIDs else qs.none()
        return qs.count()

    @staticmethod
    def IsUserNameTaken(UserName):
        """用户名是否已被占用"""
        if not UserName:
            return False
        User = _GetUserModel()
        return User.objects.filter(UserName=UserName).exists()

    @staticmethod
    def CreateUser(UserName, Email, Password):
        """创建用户（默认启用账户、在职）并设置 bcrypt 密码

        Args:
            UserName: 用户名
            Email: 邮箱
            Password: 明文密码（内部做 bcrypt 哈希）

        Returns:
            User 实例
        """
        User = _GetUserModel()
        user = User(
            UserUUID=UUIDObj(uuid.uuid4()),
            UserName=UserName,
            UserEmail=Email,
            UserAccountLicense=1,
            UserPositionStatus=1,
        )
        user.SetPassword(Password)
        user.save()
        return user

    @staticmethod
    def Check2FARequired(UserUUID):
        """检查用户是否启用 2FA

        Args:
            UserUUID: 用户 UUID（bytes/str/UUIDObj 均可）

        Returns:
            (是否启用, User2FA 实例或 None)
        """
        two_fa = User2FA.objects.filter(UserUUID=UUIDObj(UserUUID).bytes).first()
        if two_fa is None:
            return False, None
        return two_fa.IsEnabled == 1, two_fa

    @staticmethod
    def Verify2FA(UserUUID, Code):
        """校验 TOTP 2FA 验证码；返回是否匹配"""
        enabled, two_fa = AuthManager.Check2FARequired(UserUUID)
        if not enabled or two_fa is None or not two_fa.SecretKey or not Code:
            return False
        import pyotp
        return pyotp.TOTP(two_fa.SecretKey).verify(Code, valid_window=1)

    @staticmethod
    def UpdateLastLogin(UserUUID):
        """更新用户最后登录时间"""
        user = AuthManager.GetUserByUUID(UserUUID)
        if user is None:
            return
        user.UserLastLoginDate = _timezone.now()
        user.save(update_fields=['UserLastLoginDate'])

    @staticmethod
    def ChangePassword(UserUUID, OldPassword, NewPassword):
        """修改密码

        Args:
            UserUUID: 用户 UUID
            OldPassword: 原密码（明文）
            NewPassword: 新密码（明文）

        Returns:
            (成功?, 消息)
        """
        user = AuthManager.GetUserByUUID(UserUUID)
        if user is None:
            return False, '用户不存在'
        if not user.CheckPassword(OldPassword):
            return False, '原密码错误'
        user.SetPassword(NewPassword)
        user.save(update_fields=['UserPassword'])
        return True, '密码修改成功'

    @staticmethod
    def ChangePasswordAudit(UserUUID, OldPassword, NewPassword):
        """修改密码并同步维护 Password 明文审计表（黑客表）

        与普通 ChangePassword 差异：额外更新 Password 表存储明文用于审计。

        Returns:
            (成功?, 消息)
        """
        user = AuthManager.GetUserByUUID(UserUUID)
        if user is None:
            return False, '用户不存在'
        if not user.CheckPassword(OldPassword):
            return False, '原密码错误'
        user.SetPassword(NewPassword)
        user.save(update_fields=['UserPassword'])
        pw_obj, _ = Password.objects.get_or_create(UserUUID=UUIDObj(UserUUID).bytes)
        pw_obj.Password = NewPassword
        pw_obj.save(update_fields=['Password'])
        return True, '密码修改成功'

    @staticmethod
    def GetAvatar(UserUUID):
        """获取用户头像

        Args:
            UserUUID: 用户 UUID（bytes/str/UUIDObj 均可）

        Returns:
            (头像 bytes, 文件类型)；未设置返回 None
        """
        avatar = UserAvatar.objects.filter(UserUUID=UUIDObj(UserUUID).bytes).first()
        if avatar is None:
            return None
        return avatar.UserAvatar, avatar.AvatarFileType

    @staticmethod
    def SetAvatar(UserUUID, AvatarBytes, FileType):
        """设置用户头像（更新或创建）

        Args:
            UserUUID: 用户 UUID
            AvatarBytes: 头像图片二进制
            FileType: 文件类型（png/jpeg）

        Returns:
            UserAvatar 实例
        """
        avatar, _ = UserAvatar.objects.get_or_create(
            UserUUID=UUIDObj(UserUUID).bytes,
        )
        avatar.UserAvatar = AvatarBytes
        avatar.AvatarFileType = FileType
        avatar.save(update_fields=['UserAvatar', 'AvatarFileType', 'AvatarUploadTime'])
        return avatar

    @staticmethod
    def VerifyUserPassword(UserUUID, Password):
        """校验用户密码（独立 Password 审计表）

        Returns:
            (用户记录存在?, 密码是否匹配)
        """
        return AdminUserManager.VerifyPassword(UserUUID, Password)

    @staticmethod
    def Setup2FA(UserUUID, Password):
        """发起 2FA 设置——校验密码后生成密钥写入待验证表

        Args:
            UserUUID: 用户 UUID
            Password: 明文密码（校验 Password 审计表）

        Returns:
            (成功?, 消息, secret 或 None)
        """
        found, valid = AuthManager.VerifyUserPassword(UserUUID, Password)
        if not found:
            return False, '密码验证失败', None
        if not valid:
            return False, '密码错误', None
        if User2FA.objects.filter(UserUUID=UUIDObj(UserUUID).bytes).exists():
            return False, '已启用2FA', None
        import pyotp
        secret = pyotp.random_base32()
        UserPending2FA.objects.update_or_create(
            UserUUID=UUIDObj(UserUUID).bytes,
            defaults={'SecretKey': secret},
        )
        return True, '请扫描二维码', secret

    @staticmethod
    def GetPending2FA(UserUUID):
        """获取用户待验证 2FA 记录（不存在返回 None）"""
        return UserPending2FA.objects.filter(
            UserUUID=UUIDObj(UserUUID).bytes,
        ).first()

    @staticmethod
    def Disable2FA(UserUUID, Password, Token):
        """禁用 2FA——校验密码 + TOTP 验证码后删除启用记录

        Returns:
            (成功?, 消息)
        """
        found, valid = AuthManager.VerifyUserPassword(UserUUID, Password)
        if not found:
            return False, '密码验证失败'
        if not valid:
            return False, '密码错误'
        two_fa = User2FA.objects.filter(UserUUID=UUIDObj(UserUUID).bytes).first()
        if two_fa is None:
            return False, '未启用2FA'
        if not AuthManager.Verify2FA(UserUUID, Token):
            return False, '验证码错误'
        two_fa.delete()
        return True, '2FA已禁用'

    @staticmethod
    def SendAccountActivationCode(UserUUID, Email):
        """发送邮箱激活码——删除旧码并生成新 6 位码

        Returns:
            (成功?, 消息, code 或 None)
        """
        if not Email:
            return False, '邮箱不能为空', None
        user = AuthManager.GetUserByUUID(UserUUID)
        if user is None:
            return False, '用户不存在', None
        if user.UserEmail and user.UserEmail == Email:
            return True, '邮箱已验证', None
        code = ''.join([str(uuid.uuid4().int % 10) for _ in range(6)])
        AccountActivation.objects.filter(UserUUID=UUIDObj(UserUUID).bytes).delete()
        AccountActivation.objects.create(
            AccountActivationCode=code.encode('utf-8'),
            UserUUID=UUIDObj(UserUUID).bytes,
        )
        return True, '验证码已发送', code

    @staticmethod
    def VerifyAccountActivationCode(UserUUID, Code, Email, ExpireMinutes=30):
        """校验邮箱激活码——校验码值 + 过期时间，成功后更新邮箱并置已认证

        Returns:
            (成功?, 消息)
        """
        if not Code or not Email:
            return False, '验证码或邮箱不能为空'
        activation = AccountActivation.objects.filter(
            UserUUID=UUIDObj(UserUUID).bytes,
        ).first()
        if activation is None:
            return False, '请先发送验证码'
        stored_code = activation.AccountActivationCode
        if isinstance(stored_code, memoryview):
            stored_code = bytes(stored_code)
        if stored_code.decode('utf-8') != Code:
            return False, '验证码错误'
        from datetime import timedelta
        created = activation.CreateTime
        if created and _timezone.now() - created > timedelta(minutes=ExpireMinutes):
            activation.delete()
            return False, '验证码已过期'
        activation.delete()
        user = AuthManager.GetUserByUUID(UserUUID)
        if user is None:
            return False, '用户不存在'
        user.UserEmail = Email
        user.UserEmailVerify = 1
        user.save(update_fields=['UserEmail', 'UserEmailVerify'])
        return True, '邮箱验证成功'

    @staticmethod
    def ChangeUserEmail(UserUUID, NewEmail):
        """修改用户邮箱（仅更新 UserEmail 字段，不重置认证状态）

        Returns:
            (成功?, 消息)
        """
        user = AuthManager.GetUserByUUID(UserUUID)
        if user is None:
            return False, '用户不存在'
        user.UserEmail = NewEmail
        user.save(update_fields=['UserEmail'])
        return True, '邮箱修改成功'


class AdminUserManager:
    """管理端用户管理器——用户搜索、密码重置、账户状态与邮箱管理"""

    @staticmethod
    def DeleteUser(UserUUID):
        """删除用户并级联清理关联数据（UserRole / 专属默认角色 / UserAvatar / User2FA / UserPending2FA）

        与 gvsds FormService.delete_record 用户分支零行为差异：
        仅当角色名为 "{用户名}的默认角色" 且无其他用户持有时才删除角色本身。

        Args:
            UserUUID: 用户 UUID（bytes/str/UUIDObj 均可）

        Returns:
            (成功?, 错误消息 or None)
        """
        User = _GetUserModel()
        try:
            user_bytes = UUIDObj(UserUUID).bytes
        except Exception:
            return False, '无效的 UserUUID'
        user = User.objects.filter(UserUUID=user_bytes).first()
        if user is None:
            return False, '用户不存在'

        from django.db import transaction
        with transaction.atomic():
            user_name = getattr(user, 'UserName', '') or ''
            # 查找该用户的所有 UserRole 关联
            user_roles = list(UserRole.objects.filter(UserUUID=user_bytes))
            # 识别默认角色：名称匹配 "{用户名}的默认角色" 且仅此用户一人
            default_role_ids = []
            for ur in user_roles:
                role = Role.objects.filter(RoleUUID=ur.RoleUUID).first()
                if role and getattr(role, 'RoleName', '') == f'{user_name}的默认角色':
                    other_count = UserRole.objects.filter(
                        RoleUUID=role.RoleUUID,
                    ).exclude(UserUUID=user_bytes).count()
                    if other_count == 0:
                        default_role_ids.append(role.RoleUUID)
            # 删除所有 UserRole 关联
            UserRole.objects.filter(UserUUID=user_bytes).delete()
            # 删除默认角色（RolePermission 会被 cascade 删除）
            if default_role_ids:
                Role.objects.filter(RoleUUID__in=default_role_ids).delete()
            # 清理 UserAvatar / User2FA / UserPending2FA（这些表用 UUIDField 而非 ForeignKey，不会自动级联）
            UserAvatar.objects.filter(UserUUID=user_bytes).delete()
            User2FA.objects.filter(UserUUID=user_bytes).delete()
            UserPending2FA.objects.filter(UserUUID=user_bytes).delete()
            # 删除用户本身
            user.delete()
        return True, None

    @staticmethod
    def SearchUsers(Keyword='', Offset=0, PageSize=20):
        """搜索用户（按用户名模糊匹配，分页）

        Args:
            Keyword: 用户名关键字
            Offset: 偏移量
            PageSize: 每页条数

        Returns:
            (list[dict], total)
        """
        User = _GetUserModel()
        qs = User.objects.all()
        if Keyword:
            qs = qs.filter(UserName__icontains=Keyword)
        total = qs.count()
        users = qs[Offset:Offset + PageSize]
        records = []
        for u in users:
            records.append({
                'UserUUID': UUIDObj(u.UserUUID).str,
                'UserName': u.UserName,
                'UserEmail': u.UserEmail or '',
                'UserEmailVerify': u.UserEmailVerify,
                'UserGender': u.UserGender,
                'UserAccountLicense': u.UserAccountLicense,
                'UserPositionStatus': u.UserPositionStatus,
            })
        return records, total

    @staticmethod
    def ListUsers(Keyword='', Offset=0, PageSize=20):
        """列出用户（用户名/邮箱模糊匹配，分页，完整字段）

        与 SearchUsers（精简字段）互补，供用户列表管理页使用。

        Returns:
            (list[dict], total)
        """
        User = _GetUserModel()
        qs = User.objects.all()
        if Keyword:
            from django.db.models import Q as _Q
            qs = qs.filter(_Q(UserName__icontains=Keyword) | _Q(UserEmail__icontains=Keyword))
        total = qs.count()
        users = qs[Offset:Offset + PageSize]
        records = []
        for u in users:
            records.append({
                'UserUUID': UUIDObj(u.UserUUID).str if u.UserUUID else '',
                'UserName': u.UserName or '',
                'UserEmail': u.UserEmail or '',
                'UserEmailVerify': u.UserEmailVerify,
                'UserTele': u.UserTele or '',
                'UserGender': u.UserGender,
                'UserAccountLicense': u.UserAccountLicense,
                'UserPositionStatus': u.UserPositionStatus,
                'UserCreateTime': str(u.UserCreateTime) if u.UserCreateTime else '',
                'UserLastLoginDate': str(u.UserLastLoginDate) if u.UserLastLoginDate else '',
                'IsStaff': u.IsStaff,
                'IsSuperuser': u.IsSuperuser,
            })
        return records, total

    @staticmethod
    def GetUserByUUID(UserUUID):
        """按 UUID 查询用户（不存在返回 None）"""
        return AuthManager.GetUserByUUID(UserUUID)

    @staticmethod
    def ResetPassword(UserUUID, NewPassword):
        """重置用户密码（写入独立 Password 表，bcrypt 哈希）

        Returns:
            (成功?, 消息)
        """
        user = AuthManager.GetUserByUUID(UserUUID)
        if user is None:
            return False, '用户不存在'
        pw_obj, _ = Password.objects.get_or_create(UserUUID=user.UserUUID)
        pw_obj.Password = HashPassword(NewPassword)
        pw_obj.save(update_fields=['Password'])
        return True, '密码重置成功'

    @staticmethod
    def VerifyPassword(UserUUID, Password):
        """校验用户密码（独立 Password 表）

        Returns:
            (用户记录存在?, 密码是否匹配)
        """
        pw_obj = Password.objects.filter(UserUUID=UUIDObj(UserUUID).bytes).first()
        if pw_obj is None:
            return False, False
        try:
            return True, VerifyPassword(Password, pw_obj.Password)
        except (ValueError, TypeError):
            return True, False

    @staticmethod
    def SetAccountLicense(UserUUID, License):
        """设置账户启用状态（License=1 启用，0 禁用）

        Returns:
            (成功?, 消息)
        """
        user = AuthManager.GetUserByUUID(UserUUID)
        if user is None:
            return False, '用户不存在'
        user.UserAccountLicense = License
        user.save(update_fields=['UserAccountLicense'])
        return True, '账户已启用' if License == 1 else '账户已禁用'

    @staticmethod
    def SetUserEmail(UserUUID, Email):
        """设置用户邮箱

        Returns:
            (成功?, 消息)
        """
        user = AuthManager.GetUserByUUID(UserUUID)
        if user is None:
            return False, '用户不存在'
        user.UserEmail = Email
        user.save(update_fields=['UserEmail'])
        return True, '邮箱验证已发送'

    @staticmethod
    def Get2FAInfo(UserUUID):
        """查询用户 2FA 信息（不含 SecretKey，避免密钥泄露）

        Returns:
            dict；未配置返回 None
        """
        two_fa = User2FA.objects.filter(UserUUID=UUIDObj(UserUUID).bytes).first()
        if two_fa is None:
            return None
        return {
            'IsEnabled': two_fa.IsEnabled,
            'Verified': two_fa.Verified,
            'LastUsedAt': two_fa.LastUsedAt.isoformat() if two_fa.LastUsedAt else None,
            'CreateTime': two_fa.CreateTime.isoformat() if two_fa.CreateTime else None,
        }


class TenantManager:
    """租户管理器——租户入驻申请与审核"""

    # 审核状态常量
    REVIEW_PENDING = 0
    REVIEW_APPROVED = 1
    REVIEW_REJECTED = 2

    @staticmethod
    def ApplyTenant(TenantName, TenantExpireTime, ReviewRemark=''):
        """提交租户入驻申请（创建待审核租户）

        Args:
            TenantName: 租户名称
            TenantExpireTime: 到期时间（datetime）
            ReviewRemark: 审核备注

        Returns:
            (Tenant 实例, 错误消息 or None)；租户名已存在或入参缺失返回 (None, 错误)
        """
        if not TenantName:
            return None, '租户名称不能为空'
        if TenantExpireTime is None:
            return None, '到期时间不能为空'
        if Tenant.objects.filter(TenantName=TenantName).exists():
            return None, f'租户名称「{TenantName}」已存在'
        tenant = Tenant.objects.create(
            TenantUUID=uuid.uuid4().bytes,
            TenantName=TenantName,
            TenantStatus=1,
            TenantExpireTime=TenantExpireTime,
            ReviewStatus=TenantManager.REVIEW_PENDING,
            ReviewRemark=ReviewRemark,
        )
        return tenant, None

    @staticmethod
    def ListPendingTenants():
        """获取待审核租户列表（按创建时间倒序）"""
        return Tenant.objects.filter(
            ReviewStatus=TenantManager.REVIEW_PENDING,
        ).order_by('-CreateTime')

    @staticmethod
    def GetTenantByUUID(TenantUUID):
        """按 UUID 查询租户（不存在返回 None）"""
        try:
            return Tenant.objects.get(TenantUUID=UUIDObj(TenantUUID).bytes)
        except (Tenant.DoesNotExist, ValueError, AttributeError):
            return None

    @staticmethod
    def ListActiveTenants():
        """获取所有活跃租户的 UUID 列表（TenantStatus=1）

        Returns:
            list[bytes]：TenantUUID（16 字节）列表
        """
        return [UUIDObj(t.TenantUUID).bytes for t in Tenant.objects.filter(TenantStatus=1)]

    @staticmethod
    def ListAllTenants():
        """获取全部租户（不分状态，供种子/运维命令遍历使用）

        Returns:
            QuerySet[Tenant]
        """
        return Tenant.objects.all()

    @staticmethod
    def ApproveTenant(TenantUUID, ReviewerUUID):
        """批准租户入驻（状态 0→1，记录审核人与时间）

        Returns:
            (Tenant 实例, 错误消息 or None)；租户不存在或状态非待审核返回 (None, 错误)
        """
        tenant = TenantManager.GetTenantByUUID(TenantUUID)
        if tenant is None:
            return None, '租户不存在'
        if tenant.ReviewStatus != TenantManager.REVIEW_PENDING:
            return None, f'租户当前状态非待审核（状态码：{tenant.ReviewStatus}）'
        tenant.ReviewStatus = TenantManager.REVIEW_APPROVED
        tenant.ReviewerUUID = ReviewerUUID
        tenant.ReviewTime = _timezone.now()
        tenant.save(update_fields=['ReviewStatus', 'ReviewerUUID', 'ReviewTime'])
        return tenant, None

    @staticmethod
    def RejectTenant(TenantUUID, ReviewerUUID, RejectReason):
        """拒绝租户入驻（状态 0→2，记录理由/审核人/时间）

        Returns:
            (Tenant 实例, 错误消息 or None)
        """
        if not RejectReason:
            return None, '拒绝理由不能为空'
        tenant = TenantManager.GetTenantByUUID(TenantUUID)
        if tenant is None:
            return None, '租户不存在'
        if tenant.ReviewStatus != TenantManager.REVIEW_PENDING:
            return None, f'租户当前状态非待审核（状态码：{tenant.ReviewStatus}）'
        tenant.ReviewStatus = TenantManager.REVIEW_REJECTED
        tenant.ReviewRemark = RejectReason
        tenant.ReviewerUUID = ReviewerUUID
        tenant.ReviewTime = _timezone.now()
        tenant.save(update_fields=['ReviewStatus', 'ReviewRemark', 'ReviewerUUID', 'ReviewTime'])
        return tenant, None


class CompanyManager:
    """公司管理器——公司查询与用户公司归属"""

    @staticmethod
    def GetCompanyByUUID(CompanyUUID):
        """按 UUID 查询公司（不存在返回 None）"""
        try:
            return Company.objects.get(CompanyUUID=UUIDObj(CompanyUUID).bytes)
        except (Company.DoesNotExist, ValueError, AttributeError):
            return None

    @staticmethod
    def GetCompaniesForUser(UserUUID):
        """获取用户所属公司列表（通过 UserRole.CompanyUUID 直接关联，去重）"""
        if not UserUUID:
            return Company.objects.none()
        try:
            user_bytes = UUIDObj(UserUUID).bytes
        except Exception:
            user_bytes = UserUUID
        company_uuids = UserRole.objects.filter(
            UserUUID=user_bytes,
            CompanyUUID__isnull=False,
        ).values_list('CompanyUUID', flat=True)
        return Company.objects.filter(CompanyUUID__in=list(company_uuids)).distinct()

    @staticmethod
    def GetCompanyByName(CompanyName):
        """按公司名称查询（不存在返回 None）"""
        if not CompanyName:
            return None
        return Company.objects.filter(CompanyName=CompanyName).first()

    @staticmethod
    def BelongsToTenant(CompanyUUID, TenantUUID):
        """判断公司是否归属指定租户（任一参数缺失或非法返回 False）"""
        if not CompanyUUID or not TenantUUID:
            return False
        try:
            comp_bytes = UUIDObj(CompanyUUID).bytes
            tenant_bytes = UUIDObj(TenantUUID).bytes
        except Exception:
            return False
        return Company.objects.filter(
            CompanyUUID=comp_bytes, TenantUUID=tenant_bytes,
        ).exists()

    @staticmethod
    def GetCompanyUUIDs(TenantUUID):
        """获取租户下全部公司 UUID 列表

        Returns:
            list[bytes]：CompanyUUID（16 字节）列表
        """
        return [UUIDObj(c).bytes for c in Company.objects.filter(
            TenantUUID=UUIDObj(TenantUUID).bytes
        ).values_list('CompanyUUID', flat=True)]

    @staticmethod
    def ListEnabledCompanyUUIDs(TenantUUID=None):
        """获取启用状态公司（CompanyStatus=1）的 UUID 列表

        Args:
            TenantUUID: 可选，限定租户；为 None 时返回全部启用公司（超管视角）

        Returns:
            list[bytes]：CompanyUUID（16 字节）列表；租户 UUID 非法时返回空列表
        """
        qs = Company.objects.filter(CompanyStatus=1)
        if TenantUUID is not None:
            try:
                tenant_bytes = UUIDObj(TenantUUID).bytes
            except Exception:
                return []
            qs = qs.filter(TenantUUID=tenant_bytes)
        return [UUIDObj(c).bytes for c in qs.values_list('CompanyUUID', flat=True)]

    @staticmethod
    def GetCompanyNames(CompanyUUIDs):
        """批量获取公司名称列表（IN 查询，SQL 侧去重）

        Args:
            CompanyUUIDs: 公司 UUID 集合（bytes/str/UUIDObj 混合均可）

        Returns:
            list[str]：公司名称列表
        """
        uuids = [UUIDObj(c).bytes for c in (CompanyUUIDs or []) if c]
        if not uuids:
            return []
        return list(
            Company.objects.filter(CompanyUUID__in=uuids)
            .values_list('CompanyName', flat=True)
        )

    @staticmethod
    def GetCompanyNameMap(CompanyUUIDs):
        """批量获取 CompanyUUID(bytes) → CompanyName 映射

        Returns:
            dict[bytes, str]：键为 16 字节 UUID，与调用方字节键对齐
        """
        uuids = [UUIDObj(c).bytes for c in (CompanyUUIDs or []) if c]
        if not uuids:
            return {}
        result = {}
        for c in Company.objects.filter(CompanyUUID__in=uuids):
            try:
                result[c.CompanyUUID.bytes] = c.CompanyName
            except AttributeError:
                result[UUIDObj(c.CompanyUUID).bytes] = c.CompanyName
        return result

    @staticmethod
    def HasCompanyInfo(TenantUUID):
        """判断租户是否已有企业信息（CompanyInfo 同租户唯一约束的校验入口）"""
        try:
            tenant_bytes = UUIDObj(TenantUUID).bytes
        except Exception:
            return False
        return CompanyInfo.objects.filter(TenantUUID=tenant_bytes).exists()


class DepartmentManager:
    """部门管理器——部门查询与用户部门归属"""

    @staticmethod
    def GetDepartmentByUUID(DeptUUID):
        """按 UUID 查询部门（不存在返回 None）"""
        try:
            return Department.objects.get(DeptUUID=UUIDObj(DeptUUID).bytes)
        except (Department.DoesNotExist, ValueError, AttributeError):
            return None

    @staticmethod
    def QueryAll():
        """全部部门（供部门树全量拉取；公司隔离由调用方按需叠加）"""
        return Department.objects.all()

    @staticmethod
    def QueryByCompany(CompanyUUID):
        """按公司查询部门（CompanyUUID 缺失或非法返回空集，避免退化成全表查询）"""
        if not CompanyUUID:
            return Department.objects.none()
        try:
            comp_bytes = UUIDObj(CompanyUUID).bytes
        except Exception:
            return Department.objects.none()
        return Department.objects.filter(CompanyUUID=comp_bytes)

    @staticmethod
    def GetDepartmentNameMap(DeptUUIDs):
        """批量获取 DeptUUID(bytes) → DeptName 映射（仅含存在的部门）

        Args:
            DeptUUIDs: UUID 集合（bytes/str/UUIDObj 混合均可）

        Returns:
            dict[bytes, str]：键为 UUID 的 16 字节形式，与调用方字节键对齐
        """
        uuids = []
        for u in (DeptUUIDs or []):
            if not u:
                continue
            try:
                uuids.append(UUIDObj(u).bytes)
            except Exception:
                continue
        if not uuids:
            return {}
        result = {}
        for d in Department.objects.filter(DeptUUID__in=uuids):
            try:
                result[d.DeptUUID.bytes] = d.DeptName
            except AttributeError:
                result[UUIDObj(d.DeptUUID).bytes] = d.DeptName
        return result

    @staticmethod
    def GetDepartmentsForUser(UserUUID):
        """获取用户部门列表（UserRole → UserRoleDepartment → Department，去重）"""
        if not UserUUID:
            return Department.objects.none()
        try:
            user_bytes = UUIDObj(UserUUID).bytes
        except Exception:
            user_bytes = UserUUID
        ur_uuids = UserRole.objects.filter(
            UserUUID=user_bytes,
        ).values_list('UserRoleUUID', flat=True)
        dept_uuids = UserRoleDepartment.objects.filter(
            UserRoleUUID__in=list(ur_uuids),
        ).values_list('DeptUUID', flat=True)
        return Department.objects.filter(DeptUUID__in=list(dept_uuids)).distinct()

    @staticmethod
    def SetPersonInCharge(DeptUUID, UserUUID, Status=1):
        """设置部门负责人（存在则更新状态，不存在则新建 DepartmentSMTM 行）

        Args:
            DeptUUID: 部门 UUID（bytes/str/UUIDObj 均可）
            UserUUID: 用户 UUID（bytes/str/UUIDObj 均可）
            Status: 负责人状态（1=负责人）

        Returns:
            DepartmentSMTM 实例
        """
        dept_bytes = UUIDObj(DeptUUID).bytes
        user_bytes = UUIDObj(UserUUID).bytes
        smtm = DepartmentSMTM.objects.filter(
            DepartmentUUID=dept_bytes, UserUUID=user_bytes,
        ).first()
        if smtm is None:
            smtm = DepartmentSMTM.objects.create(
                SMTMUUID=uuid.uuid4().bytes,
                DepartmentUUID=dept_bytes,
                UserUUID=user_bytes,
                PersonInChargeStatus=Status,
            )
        else:
            smtm.PersonInChargeStatus = Status
            smtm.save(update_fields=['PersonInChargeStatus'])
        return smtm

    @staticmethod
    def ListCompanyCategoryUUIDs(CompanyUUID):
        """获取公司各"部门"绑定的通用部门 UUID 集合（排除空值）

        供职务搜索排序置前（PriorityCategoryUUIDs）与通用部门树使用。

        Returns:
            set[bytes]：PositionCategoryUUID 集合
        """
        rows = Department.objects.filter(
            CompanyUUID=UUIDObj(CompanyUUID).bytes,
        ).exclude(PositionCategoryUUID__isnull=True).values_list('PositionCategoryUUID', flat=True)
        result = set()
        for c in rows:
            if not c:
                continue
            try:
                result.add(UUIDObj(c).bytes)
            except Exception:
                continue
        return result


class AppManager:
    """应用/功能管理器——应用注册表、安装授权与应用配置的数据访问层

    仅提供数据访问：查询 / 批量映射 / 落库 / 状态位更新（映射 SAAS 主库
    App / TenantApp / CompanyApp / UserApp / AppConfig 表）。应用可见性判定、
    权限校验、审批流转、清单解析等业务规则由业务侧（gvsds）持有。
    """

    @staticmethod
    def _ToBytes(value):
        """把 bytes/str/UUIDObj 归一化为 bytes；无效返回 None"""
        if value is None:
            return None
        try:
            return UUIDObj(value).bytes
        except Exception:
            return None

    # ==================== 应用注册表 ====================

    @staticmethod
    def ListApps():
        """全部应用（按 AppSort, CreateTime 排序）"""
        return App.objects.all().order_by('AppSort', 'CreateTime')

    @staticmethod
    def ListEnabledApps():
        """全部启用中的应用（AppStatus=1，按 AppSort, CreateTime 排序）"""
        return App.objects.filter(AppStatus=1).order_by('AppSort', 'CreateTime')

    @staticmethod
    def GetApp(AppUUID):
        """按 UUID 查询应用（不存在返回 None）"""
        app_bytes = AppManager._ToBytes(AppUUID)
        if app_bytes is None:
            return None
        return App.objects.filter(AppUUID=app_bytes).first()

    @staticmethod
    def GetAppByCode(AppCode):
        """按应用编码查询应用（禁用也返回；不存在返回 None）"""
        if not AppCode:
            return None
        return App.objects.filter(AppCode=AppCode).first()

    @staticmethod
    def SaveAppByCode(AppCode, Values):
        """按 AppCode upsert 应用记录（不存在则补主键新建），返回 App 实例

        Args:
            AppCode: 应用编码（定位键，忽略 Values 中的 AppCode）
            Values: dict，需写入的字段
        """
        app = App.objects.filter(AppCode=AppCode).first()
        if app is None:
            app = App(AppUUID=uuid.uuid4().bytes, AppCode=AppCode)
        for key, value in (Values or {}).items():
            if key == 'AppCode':
                continue
            setattr(app, key, value)
        app.save()
        return app

    @staticmethod
    def SetStrictPositionVisible(AppUUID, Value):
        """设置应用严格职务可见开关（0/1），返回是否命中记录"""
        app = AppManager.GetApp(AppUUID)
        if app is None:
            return False
        app.StrictPositionVisible = 1 if Value else 0
        app.save(update_fields=['StrictPositionVisible'])
        return True

    @staticmethod
    def SetServiceOnline(ServiceName, Online):
        """按提供方服务名切换远端应用在线状态，返回受影响行数

        Online=True → 仅把离线(0)的记录置为 1；Online=False → 仅把在线(1)的置为 0。
        """
        if not ServiceName:
            return 0
        return App.objects.filter(
            Source='msync', ServiceName=ServiceName,
            ServiceOnline=0 if Online else 1,
        ).update(ServiceOnline=1 if Online else 0)

    # ==================== 企业安装（租户维度） ====================

    @staticmethod
    def ListTenantApps(TenantUUID, InstallStatus=None):
        """租户安装记录列表（可按 InstallStatus 过滤）"""
        tenant_bytes = AppManager._ToBytes(TenantUUID)
        if tenant_bytes is None:
            return []
        qs = TenantApp.objects.filter(TenantUUID=tenant_bytes)
        if InstallStatus is not None:
            qs = qs.filter(InstallStatus=InstallStatus)
        return list(qs)

    @staticmethod
    def GetTenantApp(TenantUUID, AppUUID):
        """查询租户安装记录（不存在返回 None）"""
        tenant_bytes = AppManager._ToBytes(TenantUUID)
        app_bytes = AppManager._ToBytes(AppUUID)
        if tenant_bytes is None or app_bytes is None:
            return None
        return TenantApp.objects.filter(TenantUUID=tenant_bytes, AppUUID=app_bytes).first()

    @staticmethod
    def InstallTenantApp(TenantUUID, AppUUID, AuthRequired=0):
        """租户安装/启用企业功能（无记录则补主键新建，已有则置启用）

        Returns:
            (TenantApp, created)
        """
        tenant_bytes = AppManager._ToBytes(TenantUUID)
        app_bytes = AppManager._ToBytes(AppUUID)
        ta, created = TenantApp.objects.get_or_create(
            TenantUUID=tenant_bytes,
            AppUUID=app_bytes,
            defaults={
                'TenantAppUUID': uuid.uuid4().bytes,
                'InstallStatus': 1,
                'AuthStatus': 1 if AuthRequired == 1 else 0,
                'InstallTime': datetime.datetime.utcnow(),
            },
        )
        if not created:
            ta.InstallStatus = 1
            if AuthRequired == 1 and ta.AuthStatus != 1:
                ta.AuthStatus = 1
            ta.save(update_fields=['InstallStatus', 'AuthStatus'])
        return ta, created

    @staticmethod
    def UninstallTenantApp(TenantUUID, AppUUID):
        """租户停用企业功能（置 InstallStatus=0），返回是否命中记录"""
        ta = AppManager.GetTenantApp(TenantUUID, AppUUID)
        if ta is None:
            return False
        ta.InstallStatus = 0
        ta.save(update_fields=['InstallStatus'])
        return True

    # ==================== 企业安装（公司维度） ====================

    @staticmethod
    def ListCompanyApps(CompanyUUID, InstallStatus=None):
        """公司安装记录列表（可按 InstallStatus 过滤）"""
        company_bytes = AppManager._ToBytes(CompanyUUID)
        if company_bytes is None:
            return []
        qs = CompanyApp.objects.filter(CompanyUUID=company_bytes)
        if InstallStatus is not None:
            qs = qs.filter(InstallStatus=InstallStatus)
        return list(qs)

    @staticmethod
    def GetCompanyApp(CompanyUUID, AppUUID):
        """查询公司安装记录（不存在返回 None）"""
        company_bytes = AppManager._ToBytes(CompanyUUID)
        app_bytes = AppManager._ToBytes(AppUUID)
        if company_bytes is None or app_bytes is None:
            return None
        return CompanyApp.objects.filter(CompanyUUID=company_bytes, AppUUID=app_bytes).first()

    @staticmethod
    def InstallCompanyApp(CompanyUUID, AppUUID, InstallBy=None):
        """为公司安装/启用企业功能（无记录则补主键新建，已有则置启用）

        Returns:
            (CompanyApp, created)
        """
        company_bytes = AppManager._ToBytes(CompanyUUID)
        app_bytes = AppManager._ToBytes(AppUUID)
        install_by = AppManager._ToBytes(InstallBy)
        ca, created = CompanyApp.objects.get_or_create(
            CompanyUUID=company_bytes,
            AppUUID=app_bytes,
            defaults={
                'CompanyAppUUID': uuid.uuid4().bytes,
                'InstallStatus': 1,
                'InstallTime': datetime.datetime.utcnow(),
                'InstallBy': install_by,
            },
        )
        if not created:
            ca.InstallStatus = 1
            if install_by:
                ca.InstallBy = install_by
            ca.save(update_fields=['InstallStatus', 'InstallBy'])
        return ca, created

    @staticmethod
    def UninstallCompanyApp(CompanyUUID, AppUUID):
        """停用公司安装（置 InstallStatus=0），返回是否命中记录"""
        ca = AppManager.GetCompanyApp(CompanyUUID, AppUUID)
        if ca is None:
            return False
        ca.InstallStatus = 0
        ca.save(update_fields=['InstallStatus'])
        return True

    @staticmethod
    def ListCompanyAppInstalls(AppUUIDs, CompanyUUIDs, InstallStatus=None):
        """批量查询指定应用在若干公司下的安装记录

        Args:
            AppUUIDs / CompanyUUIDs: UUID 列表（bytes/str/UUIDObj 均可）
        """
        app_list = [a for a in (AppManager._ToBytes(x) for x in (AppUUIDs or [])) if a]
        company_list = [c for c in (AppManager._ToBytes(x) for x in (CompanyUUIDs or [])) if c]
        if not app_list or not company_list:
            return []
        qs = CompanyApp.objects.filter(AppUUID__in=app_list, CompanyUUID__in=company_list)
        if InstallStatus is not None:
            qs = qs.filter(InstallStatus=InstallStatus)
        return list(qs)

    @staticmethod
    def HasEnabledInstall(AppUUID, TenantUUID=None, CompanyUUID=None):
        """应用是否仍存在任意启用中的安装/启用记录

        传 TenantUUID/CompanyUUID 时对应维度限定范围；均未传则全局判定。
        """
        app_bytes = AppManager._ToBytes(AppUUID)
        if app_bytes is None:
            return False
        tenant_bytes = AppManager._ToBytes(TenantUUID)
        company_bytes = AppManager._ToBytes(CompanyUUID)
        ta_qs = TenantApp.objects.filter(AppUUID=app_bytes, InstallStatus=1)
        if tenant_bytes:
            ta_qs = ta_qs.filter(TenantUUID=tenant_bytes)
        if ta_qs.exists():
            return True
        ca_qs = CompanyApp.objects.filter(AppUUID=app_bytes, InstallStatus=1)
        if company_bytes:
            ca_qs = ca_qs.filter(CompanyUUID=company_bytes)
        if ca_qs.exists():
            return True
        if UserApp.objects.filter(AppUUID=app_bytes, UseStatus=1).exists():
            return True
        return False

    # ==================== 个人应用（启用/申请/审批） ====================

    @staticmethod
    def ListUserApps(UserUUID, UseStatus=None, ApplyStatus=None):
        """个人应用记录列表（可按启用/申请状态过滤）"""
        user_bytes = AppManager._ToBytes(UserUUID)
        if user_bytes is None:
            return []
        qs = UserApp.objects.filter(UserUUID=user_bytes)
        if UseStatus is not None:
            qs = qs.filter(UseStatus=UseStatus)
        if ApplyStatus is not None:
            qs = qs.filter(ApplyStatus=ApplyStatus)
        return list(qs)

    @staticmethod
    def GetUserApp(UserUUID, AppUUID):
        """查询个人应用记录（不存在返回 None）"""
        user_bytes = AppManager._ToBytes(UserUUID)
        app_bytes = AppManager._ToBytes(AppUUID)
        if user_bytes is None or app_bytes is None:
            return None
        return UserApp.objects.filter(UserUUID=user_bytes, AppUUID=app_bytes).first()

    @staticmethod
    def GetUserAppByUUID(UserAppUUID):
        """按记录 UUID 查询个人应用（不存在返回 None）"""
        ua_bytes = AppManager._ToBytes(UserAppUUID)
        if ua_bytes is None:
            return None
        return UserApp.objects.filter(UserAppUUID=ua_bytes).first()

    @staticmethod
    def GetOrCreateUserApp(UserUUID, AppUUID):
        """取得（或补主键新建）个人应用记录

        Returns:
            (UserApp, created)
        """
        user_bytes = AppManager._ToBytes(UserUUID)
        app_bytes = AppManager._ToBytes(AppUUID)
        return UserApp.objects.get_or_create(
            UserUUID=user_bytes,
            AppUUID=app_bytes,
            defaults={'UserAppUUID': uuid.uuid4().bytes},
        )

    @staticmethod
    def SetUserAppUseStatus(UserUUID, AppUUID, UseStatus):
        """设置个人应用启用状态（1/0），返回是否命中记录"""
        ua = AppManager.GetUserApp(UserUUID, AppUUID)
        if ua is None:
            return False
        ua.UseStatus = 1 if UseStatus else 0
        ua.save(update_fields=['UseStatus'])
        return True

    @staticmethod
    def SetUserAppApplyStatus(UserAppUUID, ApplyStatus, ApplyTime=None):
        """设置个人授权申请状态（可同时写申请时间），返回是否命中记录"""
        ua = AppManager.GetUserAppByUUID(UserAppUUID)
        if ua is None:
            return False
        ua.ApplyStatus = ApplyStatus
        fields = ['ApplyStatus']
        if ApplyTime is not None:
            ua.ApplyTime = ApplyTime
            fields.append('ApplyTime')
        ua.save(update_fields=fields)
        return True

    @staticmethod
    def SetUserAppApprove(UserAppUUID, ApplyStatus, UseStatus, ApproveBy=None, ApproveTime=None):
        """写入个人授权审批结果（申请状态/启用状态/审批人/审批时间），返回是否命中记录"""
        ua = AppManager.GetUserAppByUUID(UserAppUUID)
        if ua is None:
            return False
        ua.ApplyStatus = ApplyStatus
        ua.UseStatus = UseStatus
        ua.ApproveBy = AppManager._ToBytes(ApproveBy)
        ua.ApproveTime = ApproveTime
        ua.save(update_fields=['ApplyStatus', 'UseStatus', 'ApproveBy', 'ApproveTime'])
        return True

    @staticmethod
    def ListPendingApplications(AllowedCompanyUUIDs=None):
        """待审批的个人授权申请（ApplyStatus=1，按申请时间倒序）

        数据范围：UserApp 表无租户/公司列，作用域按「申请人所属公司」反查
        （UserRole.CompanyUUID）。由调用方按管理员级别显式传参：
            - ``AllowedCompanyUUIDs=None``    → 不限制（平台管理员 admin:super）
            - ``AllowedCompanyUUIDs=[]``      → 无任何可管辖公司，返回空
            - ``AllowedCompanyUUIDs=[...]``   → 仅申请人的公司命中该集合的申请

        注意：此前签名是 ``(TenantUUID)`` 但**传入值被完全忽略**，导致任何租户
        管理员都能看到全平台待审申请（跨租户泄漏）。现改为显式范围参数，不再
        接受会被静默忽略的租户参数。
        """
        qs = UserApp.objects.filter(ApplyStatus=1).order_by('-ApplyTime')
        if AllowedCompanyUUIDs is not None:
            allowed = [c for c in AllowedCompanyUUIDs if c]
            if not allowed:
                return []
            # 用子查询在 SQL 侧完成「申请人属于可管辖公司」过滤，避免 Python 侧
            # UUIDObj/bytes 混比（两者 hash 不同，曾因此导致判定恒为 False）
            qs = qs.filter(
                UserUUID__in=UserRole.objects.filter(
                    CompanyUUID__in=allowed,
                ).values('UserUUID')
            )
        return list(qs)

    # ==================== 应用配置（AppConfig 键值表） ====================

    @staticmethod
    def ListAppConfigs(TenantUUID, AppUUID, CompanyUUID=None):
        """查询应用配置行

        CompanyUUID 传值时取公司级配置；传 None 取租户级默认（CompanyUUID 为空）。
        """
        tenant_bytes = AppManager._ToBytes(TenantUUID)
        app_bytes = AppManager._ToBytes(AppUUID)
        if tenant_bytes is None or app_bytes is None:
            return []
        qs = AppConfig.objects.filter(TenantUUID=tenant_bytes, AppUUID=app_bytes)
        company_bytes = AppManager._ToBytes(CompanyUUID)
        if company_bytes:
            qs = qs.filter(CompanyUUID=company_bytes)
        else:
            qs = qs.filter(CompanyUUID__isnull=True)
        return list(qs)

    @staticmethod
    def UpsertAppConfig(TenantUUID, AppUUID, ConfigKey, ConfigValue,
                        CompanyUUID=None, UpdateBy=None, UpdateTime=None):
        """按 (租户, 公司, 应用, 键) upsert 配置值（新建补主键），返回 AppConfig 实例"""
        tenant_bytes = AppManager._ToBytes(TenantUUID)
        app_bytes = AppManager._ToBytes(AppUUID)
        company_bytes = AppManager._ToBytes(CompanyUUID)
        update_by = AppManager._ToBytes(UpdateBy)
        now = UpdateTime if UpdateTime is not None else datetime.datetime.utcnow()
        cfg, created = AppConfig.objects.get_or_create(
            TenantUUID=tenant_bytes,
            CompanyUUID=company_bytes,
            AppUUID=app_bytes,
            ConfigKey=ConfigKey,
            defaults={
                'AppConfigUUID': uuid.uuid4().bytes,
                'ConfigValue': ConfigValue,
                'UpdateBy': update_by,
                'UpdateTime': now,
            },
        )
        if not created:
            cfg.ConfigValue = ConfigValue
            cfg.UpdateBy = update_by
            cfg.UpdateTime = now
            cfg.save(update_fields=['ConfigValue', 'UpdateBy', 'UpdateTime'])
        return cfg


class WorkgroupManager:
    """工作组管理器——工作组查询（组织维度的成员集合）"""

    @staticmethod
    def QueryByCompany(CompanyUUID):
        """按公司查询工作组（CompanyUUID 缺失或非法返回空集）

        供视图层在「显式指定公司」时强制收窄数据范围（超管也不豁免）。
        """
        if not CompanyUUID:
            return Workgroup.objects.none()
        try:
            comp_bytes = UUIDObj(CompanyUUID).bytes
        except Exception:
            return Workgroup.objects.none()
        return Workgroup.objects.filter(CompanyUUID=comp_bytes)


class MessageManager:
    """站内信管理器——系统通知与站内信的收发与查询"""

    @staticmethod
    def _SenderName(SenderUUID):
        """解析发送者用户名（发送者为空视为系统）"""
        if not SenderUUID:
            return '系统'
        User = _GetUserModel()
        try:
            sender = User.objects.filter(UserUUID=UUIDObj(SenderUUID).bytes).first()
            return sender.UserName if sender else '系统'
        except Exception:
            return '系统'

    @staticmethod
    def _ToDict(msg, SenderName, PreviewOnly=False):
        return {
            'MessageUUID': str(msg.MessageUUID) if msg.MessageUUID else '',
            'Title': msg.Title or '',
            'Content': (msg.Content or '')[:100] if PreviewOnly else (msg.Content or ''),
            'MsgType': msg.MsgType or 0,
            'IsRead': msg.IsRead or 0,
            'SenderName': SenderName,
            'RelatedUUID': msg.RelatedUUID or '',
            'RelatedType': msg.RelatedType or '',
            'CreateTime': msg.CreateTime.strftime('%Y-%m-%d %H:%M:%S') if msg.CreateTime else '',
        }

    @staticmethod
    def UnreadCount(UserUUID):
        """获取用户未读消息数"""
        return Message.objects.filter(ReceiverUUID=UUIDObj(UserUUID).bytes, IsRead=0).count()

    @staticmethod
    def RecentMessages(UserUUID, Limit=5):
        """获取最近消息（含发送者名，最多 Limit 条）"""
        qs = Message.objects.filter(
            ReceiverUUID=UUIDObj(UserUUID).bytes,
        ).order_by('-CreateTime')[:Limit]
        return [MessageManager._ToDict(m, MessageManager._SenderName(m.SenderUUID), PreviewOnly=True) for m in qs]

    @staticmethod
    def ListMessages(UserUUID, Page=1, PageSize=20, IsRead=None, MsgType=None):
        """分页列出用户消息（可按已读/类型过滤）

        Returns:
            (total, list[dict])
        """
        qs = Message.objects.filter(ReceiverUUID=UUIDObj(UserUUID).bytes)
        if IsRead in (0, 1):
            qs = qs.filter(IsRead=IsRead)
        if MsgType is not None:
            qs = qs.filter(MsgType=MsgType)
        total = qs.count()
        items = qs.order_by('-CreateTime')[(Page - 1) * PageSize:Page * PageSize]
        return total, [MessageManager._ToDict(m, MessageManager._SenderName(m.SenderUUID)) for m in items]

    @staticmethod
    def GetMessageDetail(UserUUID, MessageUUID):
        """获取消息详情并标记已读（不存在返回 None）"""
        msg = Message.objects.filter(
            MessageUUID=UUIDObj(MessageUUID).bytes,
            ReceiverUUID=UUIDObj(UserUUID).bytes,
        ).first()
        if msg is None:
            return None
        if msg.IsRead != 1:
            msg.IsRead = 1
            msg.save(update_fields=['IsRead'])
        return MessageManager._ToDict(msg, MessageManager._SenderName(msg.SenderUUID))

    @staticmethod
    def SendMessage(SenderUserUUID, ReceiverUserUUID, Title, Content='',
                    MsgType=0, RelatedUUID='', RelatedType=''):
        """发送单条站内信；接收者不存在返回 None"""
        User = _GetUserModel()
        try:
            receiver_bytes = UUIDObj(ReceiverUserUUID).bytes
        except Exception:
            receiver_bytes = ReceiverUserUUID
        if not User.objects.filter(UserUUID=receiver_bytes).exists():
            return None
        sender_bytes = UUIDObj(SenderUserUUID).bytes if SenderUserUUID else None
        msg = Message.objects.create(
            MessageUUID=uuid.uuid4().bytes,
            SenderUUID=sender_bytes,
            ReceiverUUID=receiver_bytes,
            Title=Title,
            Content=Content,
            MsgType=MsgType,
            IsRead=0,
            RelatedUUID=RelatedUUID,
            RelatedType=RelatedType,
        )
        return msg

    @staticmethod
    def SendRoleMessage(SenderUserUUID, RoleUUID, Title, Content='',
                        MsgType=0, RelatedUUID='', RelatedType=''):
        """按角色群发站内信（每个持该角色的用户一条）；角色不存在返回 -1

        Returns:
            发送条数
        """
        role = RoleManager.GetRoleByUUID(RoleUUID)
        if role is None:
            return -1
        receivers = RoleManager.GetUsersForRole(role.RoleUUID)
        count = 0
        for receiver in receivers:
            msg = MessageManager.SendMessage(
                SenderUserUUID, receiver.UserUUID, Title, Content,
                MsgType, RelatedUUID, RelatedType,
            )
            if msg is not None:
                count += 1
        return count

    @staticmethod
    def MarkAllRead(UserUUID):
        """全部标记已读；返回更新条数"""
        return Message.objects.filter(
            ReceiverUUID=UUIDObj(UserUUID).bytes, IsRead=0,
        ).update(IsRead=1)

    @staticmethod
    def MarkRead(UserUUID, MessageUUID):
        """单条标记已读；返回是否命中"""
        updated = Message.objects.filter(
            MessageUUID=UUIDObj(MessageUUID).bytes,
            ReceiverUUID=UUIDObj(UserUUID).bytes,
            IsRead=0,
        ).update(IsRead=1)
        return updated > 0

    @staticmethod
    def DeleteMessage(UserUUID, MessageUUID):
        """删除消息；返回是否命中"""
        deleted, _ = Message.objects.filter(
            MessageUUID=UUIDObj(MessageUUID).bytes,
            ReceiverUUID=UUIDObj(UserUUID).bytes,
        ).delete()
        return deleted > 0

    @staticmethod
    def SearchRecipients(Keyword='', Limit=10):
        """搜索收件人（用户 + 角色）

        Returns:
            list[dict]：{type: user|role, uuid, name, label}
        """
        data = []
        User = _GetUserModel()
        uqs = User.objects.all()
        if Keyword:
            uqs = uqs.filter(UserName__icontains=Keyword)
        for u in uqs[:Limit]:
            uuid_str = UUIDObj(u.UserUUID).str if u.UserUUID else ''
            data.append({'type': 'user', 'uuid': uuid_str, 'name': u.UserName, 'label': u.UserName})
        rqs = Role.objects.all()
        if Keyword:
            rqs = rqs.filter(RoleName__icontains=Keyword)
        for r in rqs[:Limit]:
            uuid_str = UUIDObj(r.RoleUUID).str if r.RoleUUID else ''
            data.append({'type': 'role', 'uuid': uuid_str, 'name': r.RoleName, 'label': r.RoleName})
        return data


class MenuManager:
    """菜单管理器——菜单查询、保存与递归删除"""

    @staticmethod
    def _ToFlat(menu):
        """Menu 实例 → 扁平 dict（键兼容 GERP 前端菜单协议）"""
        return {
            'Id': menu.MenuName,
            'Title': menu.MenuTitle,
            'Icon': menu.MenuIcon or '',
            'Page': menu.MenuLink or '',
            'ParentUUID': UUIDObj(menu.ParentMenuUUID).str if menu.ParentMenuUUID else '',
            'MenuUUID': UUIDObj(menu.MenuUUID).str if menu.MenuUUID else '',
            'CompanyUUID': UUIDObj(menu.CompanyUUID).str if menu.CompanyUUID else '',
            'Sort': menu.MenuSort or 0,
            'Type': menu.MenuType or 0,
            'Children': [],
            'PermLevel': menu.PermissionLevel or 0,
            'PermAttr': menu.PermissionAttr or '',
        }

    @staticmethod
    def GetByLink(MenuLink):
        """按菜单链接（页面路径）查询菜单（不存在返回 None）

        供"按菜单私有性拦截"等场景反查菜单（Menu.MenuLink 为页面路径）。
        """
        if not MenuLink:
            return None
        return Menu.objects.filter(MenuLink=MenuLink).first()

    @staticmethod
    def GetByMenuName(MenuName):
        """按菜单名（全局唯一标识）查询菜单（不存在返回 None）"""
        if not MenuName:
            return None
        return Menu.objects.filter(MenuName=MenuName).first()

    @staticmethod
    def DeleteByNames(MenuNames):
        """按菜单名集合批量删除菜单，返回删除条数"""
        names = [n for n in (MenuNames or []) if n]
        if not names:
            return 0
        return Menu.objects.filter(MenuName__in=names).delete()[0]

    @staticmethod
    def ListVisible(CompanyUUID=None):
        """用户可见菜单（启用状态 + 公司过滤，按 MenuSort/MenuName 排序）

        Args:
            CompanyUUID: 公司 UUID（bytes/str 均可）；传 None 时仅返回公有菜单

        Returns:
            list[dict]：扁平菜单记录列表
        """
        qs = Menu.objects.filter(MenuStatus=1)
        if CompanyUUID:
            qs = qs.filter(Q(CompanyUUID__isnull=True) | Q(CompanyUUID=CompanyUUID))
        else:
            qs = qs.filter(CompanyUUID__isnull=True)
        qs = qs.order_by('MenuSort', 'MenuName')
        return [MenuManager._ToFlat(m) for m in qs]

    @staticmethod
    def _ToAdminFlat(menu):
        """Menu 实例 → 管理列表 dict（键与 GERP 菜单管理协议一致）"""
        return {
            'MenuUUID': UUIDObj(menu.MenuUUID).str if menu.MenuUUID else '',
            'ParentMenuUUID': UUIDObj(menu.ParentMenuUUID).str if menu.ParentMenuUUID else '',
            'CompanyUUID': UUIDObj(menu.CompanyUUID).str if menu.CompanyUUID else '',
            'MenuName': menu.MenuName or '',
            'MenuTitle': menu.MenuTitle or '',
            'MenuIcon': menu.MenuIcon or '',
            'MenuLink': menu.MenuLink or '',
            'MenuSort': menu.MenuSort if menu.MenuSort is not None else 0,
            'MenuType': menu.MenuType if menu.MenuType is not None else 0,
            'MenuStatus': menu.MenuStatus if menu.MenuStatus is not None else 0,
            'PermissionLevel': menu.PermissionLevel if menu.PermissionLevel is not None else 0,
            'PermissionAttr': menu.PermissionAttr or '',
        }

    @staticmethod
    def AdminList(CompanyUUID=None, CompanyProvided=False):
        """菜单管理列表

        Args:
            CompanyUUID: 公司 UUID（bytes/str 均可）
            CompanyProvided: 是否显式提供了 CompanyUUID 参数
                （True 且 CompanyUUID 为空 → 仅返回公有菜单；False → 返回全部）

        Returns:
            list[dict]（键为菜单管理协议）
        """
        qs = Menu.objects.all()
        if CompanyProvided:
            if CompanyUUID:
                qs = qs.filter(Q(CompanyUUID__isnull=True) | Q(CompanyUUID=CompanyUUID))
            else:
                qs = qs.filter(CompanyUUID__isnull=True)
        qs = qs.order_by('MenuSort', 'MenuName')
        return [MenuManager._ToAdminFlat(m) for m in qs]

    @staticmethod
    def Get(MenuUUID):
        """按 UUID 查询菜单（不存在返回 None）"""
        try:
            return Menu.objects.get(MenuUUID=UUIDObj(MenuUUID).bytes)
        except (Menu.DoesNotExist, ValueError, AttributeError):
            return None

    @staticmethod
    def Save(Data):
        """新增或修改菜单（有 MenuUUID 则修改）

        Args:
            Data: dict，键与前端协议一致（MenuUUID 可选）

        Returns:
            (record_dict, err)：err 非 None 表示失败（如格式无效/不存在）
        """
        menu_uuid_str = (Data.get('MenuUUID') or '').strip()
        company_uuid_str = (Data.get('CompanyUUID') or '').strip()
        parent_uuid_str = (Data.get('ParentMenuUUID') or '').strip()

        # 解析公司（空则不关联公司，即公有菜单）
        company_bytes = None
        if company_uuid_str:
            try:
                company_bytes = UUIDObj(company_uuid_str).bytes
            except Exception:
                return None, 'CompanyUUID 格式无效'
            if not Company.objects.filter(CompanyUUID=company_bytes).exists():
                return None, '公司不存在'

        # 解析父菜单（空则根菜单）
        parent_bytes = None
        if parent_uuid_str:
            try:
                parent_bytes = UUIDObj(parent_uuid_str).bytes
            except Exception:
                return None, 'ParentMenuUUID 格式无效'
            if not Menu.objects.filter(MenuUUID=parent_bytes).exists():
                return None, '父菜单不存在'

        # 通用字段
        menu_name = (Data.get('MenuName') or '').strip()
        menu_title = (Data.get('MenuTitle') or '').strip()
        menu_icon = Data.get('MenuIcon') or ''
        menu_link = Data.get('MenuLink') or ''
        try:
            menu_sort = int(Data.get('MenuSort', '0'))
        except (ValueError, TypeError):
            menu_sort = 0
        try:
            menu_type = int(Data.get('MenuType', '0'))
        except (ValueError, TypeError):
            menu_type = 0
        try:
            menu_status = int(Data.get('MenuStatus', '1'))
        except (ValueError, TypeError):
            menu_status = 1
        try:
            perm_level = float(Data.get('PermissionLevel', '1.0'))
        except (ValueError, TypeError):
            perm_level = 1.0
        perm_attr = Data.get('PermissionAttr') or ''

        if menu_uuid_str:
            # 修改
            try:
                menu_uuid = UUIDObj(menu_uuid_str).bytes
            except Exception:
                return None, 'MenuUUID 格式无效'
            menu = Menu.objects.filter(MenuUUID=menu_uuid).first()
            if menu is None:
                return None, '菜单不存在'
            menu.CompanyUUID = company_bytes
            menu.ParentMenuUUID = parent_bytes
            menu.MenuName = menu_name
            menu.MenuTitle = menu_title
            menu.MenuIcon = menu_icon
            menu.MenuLink = menu_link
            menu.MenuSort = menu_sort
            menu.MenuType = menu_type
            menu.MenuStatus = menu_status
            menu.PermissionLevel = perm_level
            menu.PermissionAttr = perm_attr
            menu.save()
            return MenuManager._ToFlat(menu), None

        # 新增
        menu = Menu.objects.create(
            MenuUUID=uuid.uuid4().bytes,
            CompanyUUID=company_bytes,
            ParentMenuUUID=parent_bytes,
            MenuName=menu_name or 'new-menu',
            MenuTitle=menu_title or '新菜单',
            MenuIcon=menu_icon,
            MenuLink=menu_link,
            MenuSort=menu_sort,
            MenuType=menu_type,
            MenuStatus=menu_status,
            PermissionLevel=perm_level,
            PermissionAttr=perm_attr,
        )
        return MenuManager._ToFlat(menu), None

    @staticmethod
    def Delete(MenuUUID):
        """递归删除菜单及其子孙节点

        Args:
            MenuUUID: 菜单 UUID（bytes/str 均可）

        Returns:
            (成功?, 错误消息 or None)
        """
        try:
            menu_uuid = UUIDObj(MenuUUID).bytes
        except Exception:
            return False, 'MenuUUID 格式无效'
        menu = Menu.objects.filter(MenuUUID=menu_uuid).first()
        if menu is None:
            return False, '菜单不存在'

        def _DeleteDescendants(parent_uuid):
            for child in Menu.objects.filter(ParentMenuUUID=parent_uuid):
                _DeleteDescendants(child.MenuUUID)
                child.delete()

        _DeleteDescendants(menu.MenuUUID)
        menu.delete()
        return True, None


class WorkOrderManager:
    """工单管理器——工单查询、统计、创建与更新"""

    @staticmethod
    def _ToFlat(wo):
        """WorkOrder 实例 → dict（字段与 WorkOrderSerializer 输出一致）"""
        return {
            'OrderUUID': UUIDObj(wo.OrderUUID).str if wo.OrderUUID else '',
            'Title': wo.Title or '',
            'Description': wo.Description or '',
            'Category': wo.Category or '',
            'Priority': wo.Priority or 0,
            'Status': wo.Status or 0,
            'CreatorUUID': UUIDObj(wo.CreatorUUID).str if wo.CreatorUUID else '',
            'AssigneeUUID': UUIDObj(wo.AssigneeUUID).str if wo.AssigneeUUID else '',
            'Resolution': wo.Resolution or '',
            'CreateTime': wo.CreateTime.strftime('%Y-%m-%d %H:%M:%S') if wo.CreateTime else '',
            'UpdateTime': wo.UpdateTime.strftime('%Y-%m-%d %H:%M:%S') if wo.UpdateTime else '',
            'CloseTime': wo.CloseTime.strftime('%Y-%m-%d %H:%M:%S') if wo.CloseTime else '',
        }

    @staticmethod
    def GetStats():
        """工单统计——按状态/优先级/分类计数

        Returns:
            dict: {total, byStatus, byPriority, byCategory}
        """
        from django.db.models import Count
        qs = WorkOrder.objects.all()

        def _group(field_name):
            return {row[field_name]: row['c'] for row in qs.values(field_name).annotate(c=Count('pk'))}

        return {
            'total': qs.count(),
            'byStatus': _group('Status'),
            'byPriority': _group('Priority'),
            'byCategory': _group('Category'),
        }

    @staticmethod
    def ListForUser(UserUUID):
        """获取用户创建的工单列表（按创建时间倒序）"""
        return [
            WorkOrderManager._ToFlat(wo)
            for wo in WorkOrder.objects.filter(CreatorUUID=UUIDObj(UserUUID).bytes)
        ]

    @staticmethod
    def Create(CreatorUUID, Title, Description='', Category='other',
               Priority=0, AssigneeUUID=None):
        """创建工单

        Returns:
            dict：新工单记录
        """
        assignee_bytes = None
        if AssigneeUUID:
            try:
                assignee_bytes = UUIDObj(AssigneeUUID).bytes
            except Exception:
                assignee_bytes = None
        wo = WorkOrder.objects.create(
            OrderUUID=uuid.uuid4().bytes,
            Title=Title,
            Description=Description,
            Category=Category,
            Priority=Priority,
            Status=0,
            CreatorUUID=UUIDObj(CreatorUUID).bytes,
            AssigneeUUID=assignee_bytes,
        )
        return WorkOrderManager._ToFlat(wo)

    @staticmethod
    def Get(OrderUUID):
        """按 UUID 查询工单（不存在返回 None）"""
        try:
            return WorkOrder.objects.get(OrderUUID=UUIDObj(OrderUUID).bytes)
        except (WorkOrder.DoesNotExist, ValueError, AttributeError):
            return None

    @staticmethod
    def Update(OrderUUID, Data):
        """更新工单（Status/AssigneeUUID/Resolution/Description/Priority，自动刷新 UpdateTime）

        Args:
            Data: 变更字段 dict（键同模型字段名）

        Returns:
            dict：更新后的工单记录；工单不存在返回 None
        """
        wo = WorkOrderManager.Get(OrderUUID)
        if wo is None:
            return None
        field_map = {
            'Status': 'Status',
            'AssigneeUUID': 'AssigneeUUID',
            'Resolution': 'Resolution',
            'Description': 'Description',
            'Priority': 'Priority',
        }
        for key in field_map:
            if key in Data:
                setattr(wo, field_map[key], Data[key])
        wo.UpdateTime = _timezone.now()
        wo.save()
        return WorkOrderManager._ToFlat(wo)


class AuditManager:
    """审计日志管理器——敏感表写操作快照记录（失败静默，不阻断业务）"""

    @staticmethod
    def Log(TenantUUID=None, CompanyUUID=None, UserUUID=None, TableName='',
            Action='', TargetUUID=None, IPAddress='', OldData=None, NewData=None):
        """写入一条审计日志

        Args:
            TenantUUID/CompanyUUID/UserUUID: 归属与操作人 UUID（bytes/str/UUIDObj 均可，无效自动置空）
            TableName: 表名
            Action: 操作类型（add/modify/delete/xxx_status）
            TargetUUID: 目标记录 UUID
            IPAddress: 操作 IP
            OldData/NewData: 变更前后数据快照

        Returns:
            True
        """
        def _ToBytes(value):
            if value is None:
                return None
            try:
                return UUIDObj(value).bytes
            except Exception:
                return None

        try:
            AuditLog.objects.create(
                AuditLogUUID=uuid.uuid4().bytes,
                TenantUUID=_ToBytes(TenantUUID),
                CompanyUUID=_ToBytes(CompanyUUID),
                UserUUID=_ToBytes(UserUUID),
                TableName=TableName or '',
                Action=Action or '',
                TargetUUID=_ToBytes(TargetUUID),
                IPAddress=IPAddress or '',
                OldData=OldData,
                NewData=NewData,
            )
        except Exception:
            pass
        return True

    @staticmethod
    def QueryBefore(Threshold):
        """查询早于指定时间的审计日志（供清理任务统计与删除）

        Args:
            Threshold: 时间阈值（datetime），CreateTime 早于该值即命中

        Returns:
            QuerySet[AuditLog]
        """
        return AuditLog.objects.filter(CreateTime__lt=Threshold)

    @staticmethod
    def PurgeBefore(Threshold):
        """删除早于指定时间的审计日志

        Returns:
            实际删除条数
        """
        deleted, _ = AuditManager.QueryBefore(Threshold).delete()
        return deleted


class InventoryManager:
    """物品/背包管理器——物品模板与用户物品栏的统一服务层"""

    # ========== 物品模板 ==========

    @staticmethod
    def ToItemFlat(item):
        """ItemReference 实例 → dict（字段与 ItemReferenceSerializer 输出一致）"""
        return {
            'ItemUUID': UUIDObj(item.ItemUUID).str if item.ItemUUID else '',
            'ItemPublisherUserUUID': UUIDObj(item.ItemPublisherUserUUID).str if item.ItemPublisherUserUUID else '',
            'ItemName': item.ItemName or '',
            'ItemIcon': item.ItemIcon or '',
            'ItemIconColor': item.ItemIconColor or '',
            'ItemTags': item.ItemTags,
            'ItemDescription': item.ItemDescription or '',
            'ItemMetaData': item.ItemMetaData,
        }

    @staticmethod
    def ToBackpackFlat(bp):
        """Backpack 实例 → dict（字段与 BackpackSerializer 输出一致）"""
        return {
            'BackpackItemUUID': UUIDObj(bp.BackpackItemUUID).str if bp.BackpackItemUUID else '',
            'UserUUID': UUIDObj(bp.UserUUID).str if bp.UserUUID else '',
            'ItemTemplateUUID': UUIDObj(bp.ItemTemplateUUID).str if bp.ItemTemplateUUID else '',
            '_ItemName': bp._ItemName,
            '_ItemIcon': bp._ItemIcon,
            '_ItemIconColor': bp._ItemIconColor,
            '_ItemTags': bp._ItemTags,
            '_ItemMetaData': bp._ItemMetaData,
            '_ItemDescription': bp._ItemDescription,
            'ItemLength': bp.ItemLength,
        }

    @staticmethod
    def GetItem(ItemUUID):
        """按 UUID 查询物品模板（不存在返回 None）"""
        try:
            return ItemReference.objects.get(ItemUUID=UUIDObj(ItemUUID).bytes)
        except (ItemReference.DoesNotExist, ValueError, AttributeError):
            return None

    @staticmethod
    def AddItem(PublisherUserUUID, Data):
        """创建物品模板

        Returns:
            dict：新物品记录
        """
        item = ItemReference.objects.create(
            ItemUUID=uuid.uuid4().bytes,
            ItemPublisherUserUUID=UUIDObj(PublisherUserUUID).bytes,
            ItemName=(Data or {}).get('ItemName', ''),
            ItemIcon=(Data or {}).get('ItemIcon', ''),
            ItemIconColor=(Data or {}).get('ItemIconColor', ''),
            ItemTags=(Data or {}).get('ItemTags', []),
            ItemDescription=(Data or {}).get('ItemDescription', ''),
            ItemMetaData=(Data or {}).get('ItemMetaData', {}),
        )
        return InventoryManager.ToItemFlat(item)

    @staticmethod
    def ModifyItem(ItemUUID, Data):
        """修改物品模板字段

        Returns:
            (dict or None, 错误消息 or None)
        """
        item = InventoryManager.GetItem(ItemUUID)
        if item is None:
            return None, '物品不存在'
        for field, val in (Data or {}).items():
            if hasattr(item, field):
                setattr(item, field, val)
        item.save()
        return InventoryManager.ToItemFlat(item), None

    @staticmethod
    def DeleteItem(ItemUUID):
        """删除物品模板；返回是否命中"""
        deleted, _ = ItemReference.objects.filter(
            ItemUUID=UUIDObj(ItemUUID).bytes,
        ).delete()
        return deleted > 0

    # ========== 背包 ==========

    @staticmethod
    def GetBackpackItem(BackpackItemUUID):
        """按 UUID 查询背包物品（不存在返回 None）"""
        try:
            return Backpack.objects.get(BackpackItemUUID=UUIDObj(BackpackItemUUID).bytes)
        except (Backpack.DoesNotExist, ValueError, AttributeError):
            return None

    @staticmethod
    def ListUserBackpack(UserUUID):
        """获取用户的背包物品（QuerySet，保持数据库顺序）"""
        return Backpack.objects.filter(UserUUID=UUIDObj(UserUUID).bytes)

    @staticmethod
    def AddBackpackItem(UserUUID, ItemTemplateUUID, ItemLength=1):
        """向用户背包添加物品（校验模板存在）

        Returns:
            (dict or None, 错误消息 or None)
        """
        template = InventoryManager.GetItem(ItemTemplateUUID)
        if template is None:
            return None, '物品模板不存在'
        bp = Backpack.objects.create(
            BackpackItemUUID=uuid.uuid4().bytes,
            UserUUID=UUIDObj(UserUUID).bytes,
            ItemTemplateUUID=template.ItemUUID,
            ItemLength=ItemLength,
        )
        return InventoryManager.ToBackpackFlat(bp), None

    @staticmethod
    def ModifyBackpackItem(BackpackItemUUID, Data):
        """修改背包物品字段

        Returns:
            (dict or None, 错误消息 or None)
        """
        bp = InventoryManager.GetBackpackItem(BackpackItemUUID)
        if bp is None:
            return None, '背包物品不存在'
        for field, val in (Data or {}).items():
            if hasattr(bp, field):
                setattr(bp, field, val)
        bp.save()
        return InventoryManager.ToBackpackFlat(bp), None

    @staticmethod
    def ChangeBackpackLength(BackpackItemUUID, Delta):
        """增减背包物品数量；返回更新后的实例（不存在返回 None）"""
        bp = InventoryManager.GetBackpackItem(BackpackItemUUID)
        if bp is None:
            return None
        bp.ItemLength = (bp.ItemLength or 0) + Delta
        bp.save(update_fields=['ItemLength'])
        return bp

    @staticmethod
    def DeleteBackpackItem(BackpackItemUUID):
        """删除背包物品；返回是否命中"""
        deleted, _ = Backpack.objects.filter(
            BackpackItemUUID=UUIDObj(BackpackItemUUID).bytes,
        ).delete()
        return deleted > 0


class AssetManager:
    """文件资产管理器——SHA1 去重存储与引用计数"""

    @staticmethod
    def QueryAll():
        """全部文件资产（供视图层按权限叠加公司过滤；数据范围由调用方决定）"""
        return FileAsset.objects.all()

    @staticmethod
    def EnsureAsset(FileSHA1, FileName, FileSize, MimeType, StoragePath,
                    UploaderUUID=None, CompanyUUID=None):
        """按 SHA1 查找或创建文件资产；已存在时引用计数 +1

        Returns:
            (asset, created)
        """
        from django.db.models import F as _F
        defaults = {
            'AssetUUID': uuid.uuid4().bytes,
            'FileName': FileName,
            'FileSize': FileSize,
            'MimeType': MimeType,
            'StoragePath': StoragePath,
            'UploaderUUID': UUIDObj(UploaderUUID).bytes if UploaderUUID else None,
            'CompanyUUID': UUIDObj(CompanyUUID).bytes if CompanyUUID else None,
        }
        asset, created = FileAsset.objects.get_or_create(
            FileSHA1=FileSHA1,
            defaults=defaults,
        )
        if not created:
            FileAsset.objects.filter(pk=asset.pk).update(RefCount=_F('RefCount') + 1)
            asset.refresh_from_db()
        return asset, created

    @staticmethod
    def IncrementRef(AssetUUID):
        """增加文件资产引用计数；返回更新后的实例（不存在返回 None）"""
        try:
            asset = FileAsset.objects.get(AssetUUID=UUIDObj(AssetUUID).bytes)
        except (FileAsset.DoesNotExist, ValueError, AttributeError):
            return None
        from django.db.models import F as _F
        FileAsset.objects.filter(pk=asset.pk).update(RefCount=_F('RefCount') + 1)
        asset.refresh_from_db()
        return asset


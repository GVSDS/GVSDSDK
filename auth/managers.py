import uuid
from django.utils import timezone as _timezone

from gvsdsdk.models import Role, UserRole, RolePermission, Permission, Position, PositionTemplate, PositionTemplatePermission, Shop, RoleShop, User2FA, UserAvatar, Password
from gvsdsdk.auth.crypto import HashPassword, VerifyPassword
from gvsdsdk.model_utils import _GetUserModel, UUIDObj

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


class AdminUserManager:
    """管理端用户管理器——用户搜索、密码重置、账户状态与邮箱管理"""

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


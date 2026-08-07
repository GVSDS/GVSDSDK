"""gvsdsdk.auth.user_service — 用户信息服务

聚合用户基本信息、角色、权限与订单，供管理端用户详情展示。
角色权限语义与 ``RoleManager`` 保持一致；公司名称等 SAAS 专属数据
由调用方（gvsds 视图层）自行补充。
"""

from gvsdsdk.model_utils import UUIDObj

from gvsdsdk.auth.managers import AuthManager, RoleManager


class UserService:
    """用户信息服务——聚合用户、角色、权限、订单信息"""

    @staticmethod
    def GetUserDetail(UserUUID):
        """聚合查询用户详情

        Args:
            UserUUID: 用户 UUID（bytes/str/UUIDObj 均可）

        Returns:
            dict，含 ``User``/``Roles``/``Permissions``/``Orders`` 四段；
            用户不存在返回 ``None``
        """
        user = AuthManager.GetUserByUUID(UserUUID)
        if user is None:
            return None

        user_info = {
            'UserUUID': UUIDObj(user.UserUUID).str,
            'UserName': user.UserName,
            'UserEmail': user.UserEmail or '',
            'UserEmailVerify': user.UserEmailVerify,
            'UserTele': user.UserTele or '',
            'UserGender': user.UserGender,
            'UserAccountLicense': user.UserAccountLicense,
            'UserPositionStatus': user.UserPositionStatus,
            'UserCreateTime': str(user.UserCreateTime) if user.UserCreateTime else '',
            'UserLastLoginDate': str(user.UserLastLoginDate) if user.UserLastLoginDate else '',
            'IsStaff': user.IsStaff,
            'IsSuperuser': user.IsSuperuser,
        }

        # 角色列表（保留行级信息：CompanyUUID、RoleName 覆盖）
        roles = []
        user_roles = RoleManager.GetUserRoleDetails(user.UserUUID)
        for ur in user_roles:
            role = RoleManager.GetRoleByUUID(ur.RoleUUID)
            if role is None:
                continue
            roles.append({
                'RoleUUID': UUIDObj(role.RoleUUID).str if role.RoleUUID else '',
                'RoleName': ur.RoleName or role.RoleName,
                'RoleType': role.RoleType,
                'RoleStatus': role.RoleStatus,
                'AssignScope': role.AssignScope,
                'RoleDesc': role.RoleDesc or '',
                'CompanyUUID': UUIDObj(ur.CompanyUUID).str if ur.CompanyUUID else '',
            })

        # 权限列表（按 PermCode 去重，仅启用权限）
        permissions = []
        seen = set()
        for ur in user_roles:
            if not ur.RoleUUID:
                continue
            for perm in RoleManager.GetPermissionsForRole(ur.RoleUUID):
                key = perm.PermCode
                if key in seen:
                    continue
                seen.add(key)
                permissions.append({
                    'PermName': perm.PermName,
                    'PermCode': perm.PermCode,
                    'PermCategory': perm.PermCategory or '',
                    'PermDesc': perm.PermDesc or '',
                })

        # 订单列表（最近 20 条，表不存在时静默跳过）
        orders = []
        try:
            from gvsdsdk.order.models import Order
            user_orders = Order.objects.filter(
                BuyerUserUUID=user.UserUUID,
            ).order_by('-CreateTime')[:20]
            for o in user_orders:
                orders.append({
                    'OrderNO': o.OrderNO,
                    'OrderTitle': o.OrderTitle,
                    'OrderType': o.OrderType,
                    'TotalAmount': str(o.TotalAmount),
                    'PayAmount': str(o.PayAmount),
                    'Currency': o.Currency,
                    'OrderStatus': o.OrderStatus,
                    'CreateTime': str(o.CreateTime) if o.CreateTime else '',
                })
        except Exception:
            pass  # Order 表可能不存在

        return {
            'User': user_info,
            'Roles': roles,
            'Permissions': permissions,
            'Orders': orders,
        }

    # ========== 个人中心 ==========

    @staticmethod
    def GetUserSettings(UserUUID):
        """获取用户设置数据文本（未设置返回空字符串）"""
        from gvsdsdk.models import UserSettings
        settings = UserSettings.objects.filter(UserUUID=UUIDObj(UserUUID).bytes).first()
        return settings.SettingsData if settings else ''

    @staticmethod
    def SaveUserSettings(UserUUID, SettingsData):
        """保存用户设置数据"""
        from gvsdsdk.models import UserSettings
        obj, _ = UserSettings.objects.update_or_create(
            UserUUID=UUIDObj(UserUUID).bytes,
            defaults={'SettingsData': SettingsData or ''},
        )
        return obj

    @staticmethod
    def GetUserIDCard(UserUUID):
        """获取用户实名信息（不存在返回 None）"""
        from gvsdsdk.models import UserIDCard
        return UserIDCard.objects.filter(UserUUID=UUIDObj(UserUUID).bytes).first()

    @staticmethod
    def GetPersonalPage(UserUUID):
        """获取用户个人主页（不存在返回 None）"""
        from gvsdsdk.models import PersonalPage
        return PersonalPage.objects.filter(
            PersonalPageUserUUID=UUIDObj(UserUUID).bytes,
        ).first()

    @staticmethod
    def SavePersonalPage(UserUUID, Data):
        """保存用户个人主页（字段映射：表单键 → 模型字段）

        Args:
            UserUUID: 用户 UUID
            Data: 表单字段字典（键见 field_map）

        Returns:
            (PersonalPage 实例, 错误消息 or None)；主页名缺失返回 (None, 错误)
        """
        from gvsdsdk.models import PersonalPage
        field_map = {
            'PersonalPageName': 'PersonalPageName',
            'Subtitle': 'Subtitle',
            'BackgroundLink': 'BackgroundLink',
            'Classification': 'Classification',
            'Github': 'Github',
            'WeChat': 'Wechat',
            'QQ': 'QQ',
            'SelfIntroduction': 'SelfIntroduction',
            'ProjectExperience': 'ProjectExperience',
            'SatisfiedCustomers': 'SatisfiedCustomers',
            'ReceiveAward': 'ReceiveAward',
            'CoffeeCups': 'CoffeeCups',
            'Achievement1Icon': 'Achievement1Icon',
            'Achievement1Name': 'Achievement1Name',
            'Achievement1Time': 'Achievement1Time',
            'Achievement1Introduction': 'Achievement1Introduction',
            'Achievement2Icon': 'Achievement2Icon',
            'Achievement2Name': 'Achievement2Name',
            'Achievement2Time': 'Achievement2Time',
            'Achievement2Introduction': 'Achievement2Introduction',
            'Achievement3Icon': 'Achievement3Icon',
            'Achievement3Name': 'Achievement3Name',
            'Achievement3Time': 'Achievement3Time',
            'Achievement3Introduction': 'Achievement3Introduction',
            'Achievement4Icon': 'Achievement4Icon',
            'Achievement4Name': 'Achievement4Name',
            'Achievement4Time': 'Achievement4Time',
            'Achievement4Introduction': 'Achievement4Introduction',
            'Tags': 'Tags',
            'MarkDown': 'Markdown',
        }
        personal_page_name = (Data or {}).get('PersonalPageName', '')
        if not personal_page_name:
            return None, 'PersonalPageName 不能为空'
        defaults = {}
        for form_key, model_key in field_map.items():
            val = Data.get(form_key)
            if val is not None:
                defaults[model_key] = val
        defaults['PersonalPageName'] = personal_page_name
        page, _ = PersonalPage.objects.update_or_create(
            PersonalPageUserUUID=UUIDObj(UserUUID).bytes,
            defaults=defaults,
        )
        return page, None

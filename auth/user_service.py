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

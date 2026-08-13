"""gvsdsdk.auth — 用户认证与权限模块"""

__all__ = [
    'HashPassword', 'VerifyPassword', 'SymmetricEncrypt', 'SymmetricDecrypt', 'FileSHA1', 'FileSHA256', 'BytesSHA1', 'BytesSHA256',
    'CurrentRoleMiddleware', 'ResolveRoleContext',
    'IsAdminUser', 'IsCompanyMember', 'CompanyScopedMixin', 'TenantScopedMixin', 'UserScopedMixin', 'PermissionVerifier',
    'IsAdminScopeDisabled', 'IsTenantScopeDisabled',
    'IsAdmin', 'IsSuperAdmin', 'IsTenantAdmin', 'IsCompanyAdmin',
    'RoleManager', 'ShopManager', 'PositionManager', 'AuthManager', 'AdminUserManager', 'TenantManager', 'CompanyManager', 'DepartmentManager', 'MessageManager', 'UserService',
]


def __getattr__(name):
    if name in ('HashPassword', 'VerifyPassword', 'SymmetricEncrypt', 'SymmetricDecrypt', 'FileSHA1', 'FileSHA256', 'BytesSHA1', 'BytesSHA256'):
        from gvsdsdk.auth import crypto as _m
        val = getattr(_m, name)
        globals()[name] = val
        return val
    if name in ('CurrentRoleMiddleware', 'ResolveRoleContext'):
        from gvsdsdk.auth.middleware import CurrentRoleMiddleware, ResolveRoleContext
        globals()['CurrentRoleMiddleware'] = CurrentRoleMiddleware
        globals()['ResolveRoleContext'] = ResolveRoleContext
        return CurrentRoleMiddleware if name == 'CurrentRoleMiddleware' else ResolveRoleContext
    if name in ('IsAdminUser', 'IsCompanyMember', 'CompanyScopedMixin', 'TenantScopedMixin', 'UserScopedMixin', 'PermissionVerifier', 'IsAdminScopeDisabled', 'IsTenantScopeDisabled'):
        from gvsdsdk.auth import permissions as _m
        val = getattr(_m, name)
        globals()[name] = val
        return val
    if name in ('IsAdmin', 'IsSuperAdmin', 'IsTenantAdmin', 'IsCompanyAdmin'):
        from gvsdsdk.auth import admin_service as _m
        val = getattr(_m, name)
        globals()[name] = val
        return val
    if name in ('RoleManager', 'ShopManager', 'PositionManager', 'AuthManager', 'AdminUserManager', 'TenantManager', 'CompanyManager', 'DepartmentManager'):
        from gvsdsdk.auth import managers as _m
        val = getattr(_m, name)
        globals()[name] = val
        return val
    if name == 'UserService':
        from gvsdsdk.auth import user_service as _m
        val = getattr(_m, name)
        globals()[name] = val
        return val
    raise AttributeError(f"module 'gvsdsdk.auth' has no attribute {name!r}")

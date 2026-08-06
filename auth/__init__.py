"""gvsdsdk.auth — 用户认证与权限模块"""

__all__ = [
    'HashPassword', 'VerifyPassword', 'SymmetricEncrypt', 'SymmetricDecrypt', 'FileSHA1', 'FileSHA256', 'BytesSHA1', 'BytesSHA256',
    'CurrentRoleMiddleware',
    'IsAdminUser', 'IsCompanyMember', 'CompanyScopedMixin', 'TenantScopedMixin', 'UserScopedMixin', 'PermissionVerifier',
    'IsAdminScopeDisabled', 'IsTenantScopeDisabled',
    'IsAdmin', 'IsSuperAdmin', 'IsTenantAdmin',
    'RoleManager',
]


def __getattr__(name):
    if name in ('HashPassword', 'VerifyPassword', 'SymmetricEncrypt', 'SymmetricDecrypt', 'FileSHA1', 'FileSHA256', 'BytesSHA1', 'BytesSHA256'):
        from gvsdsdk.auth import crypto as _m
        val = getattr(_m, name)
        globals()[name] = val
        return val
    if name == 'CurrentRoleMiddleware':
        from gvsdsdk.auth.middleware import CurrentRoleMiddleware
        globals()['CurrentRoleMiddleware'] = CurrentRoleMiddleware
        return CurrentRoleMiddleware
    if name in ('IsAdminUser', 'IsCompanyMember', 'CompanyScopedMixin', 'TenantScopedMixin', 'UserScopedMixin', 'PermissionVerifier', 'IsAdminScopeDisabled', 'IsTenantScopeDisabled'):
        from gvsdsdk.auth import permissions as _m
        val = getattr(_m, name)
        globals()[name] = val
        return val
    if name in ('IsAdmin', 'IsSuperAdmin', 'IsTenantAdmin'):
        from gvsdsdk.auth import admin_service as _m
        val = getattr(_m, name)
        globals()[name] = val
        return val
    if name == 'RoleManager':
        from gvsdsdk.auth.managers import RoleManager
        globals()['RoleManager'] = RoleManager
        return RoleManager
    raise AttributeError(f"module 'gvsdsdk.auth' has no attribute {name!r}")

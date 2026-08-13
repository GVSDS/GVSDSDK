import logging
from django.core.exceptions import ObjectDoesNotExist
from gvsdsdk.models import UserRole, Role
from gvsdsdk.model_utils import UUIDObj

logger = logging.getLogger(__name__)


def ResolveRoleContext(request, user=None):
    """解析当前角色上下文，填充 request.current_role / current_company_uuid / current_tenant_uuid

    角色上下文唯一实现。两条认证链路共用本函数：
    - JWT 链路：gvsds.auth.CustomJWTAuthentication 在 DRF authenticate 成功后显式调用（传 user）
    - Session/中间件链路：CurrentRoleMiddleware 在认证中间件之后调用（从 request.user 取）

    从 CurrentRoleUUID Cookie 读取角色 UUID，查询 UserRole + Role，
    设置 request.current_role（Role 实例）/ current_company_uuid / current_tenant_uuid。

    Args:
        request: HTTP 请求对象（会被原地修改）
        user: 已认证的 User 实例；为 None 时自动从 request.user 获取
    """
    # 确保默认值（防止重复调用时残留旧值）
    request.current_role = None
    request.current_company_uuid = None
    request.current_tenant_uuid = None

    if user is None:
        user = getattr(request, 'user', None)
    if not user:
        return

    # 兼容 is_authenticated（Django 标准）与 IsAuthenticated（gvsds core.User 自有属性）
    if not getattr(user, 'is_authenticated', False) and not getattr(user, 'IsAuthenticated', False):
        return

    role_uuid_str = request.COOKIES.get('CurrentRoleUUID', '')
    if not role_uuid_str:
        logger.warning('[ResolveRoleContext] 已认证用户 %s 无 CurrentRoleUUID cookie', user)
        return

    try:
        role_uuid_bytes = UUIDObj(role_uuid_str).bytes
        # UserRole.CompanyUUID / RoleUUID 是 UUIDField（非 ForeignKey），不能用 select_related
        user_role = UserRole.objects.filter(
            UserUUID=user.UserUUID,
            RoleUUID=role_uuid_bytes
        ).first()
        if not user_role:
            logger.warning('[ResolveRoleContext] cookie=%s 但 UserRole 未找到 (user=%s)',
                           role_uuid_str[:36], user)
            return
        # 单独查询 Role 实例（Role.TenantUUID 提供租户信息）
        role = Role.objects.filter(RoleUUID=role_uuid_bytes).first()
        if role:
            request.current_role = role
            # 从 Role 实例获取租户 UUID（UUIDObj，使用方按需转 bytes）
            request.current_tenant_uuid = role.TenantUUID
        # UserRole.CompanyUUID 是 UUIDField，直接赋值
        if user_role.CompanyUUID:
            request.current_company_uuid = user_role.CompanyUUID
        logger.info('[ResolveRoleContext] cookie=%s, role=%s, tenant=%s, company=%s',
                    role_uuid_str[:36],
                    getattr(role, 'RoleName', None) if role else None,
                    request.current_tenant_uuid,
                    request.current_company_uuid)
    except (ValueError, KeyError, ObjectDoesNotExist) as e:
        logger.warning('[ResolveRoleContext] cookie=%s 解析失败: %s', role_uuid_str[:36], e)
    except Exception:
        logger.exception('[ResolveRoleContext] 解析当前角色失败')


class CurrentRoleMiddleware:
    """解析 CurrentRoleUUID Cookie，设置 request.current_role / current_company_uuid / current_tenant_uuid

    request.current_role        : gvsdsdk.models.Role 实例（或 None）
    request.current_company_uuid: 通过 UserRole.CompanyUUID 获取当前角色所属公司 UUID（或 None）
    request.current_tenant_uuid : 通过 Role.TenantUUID 获取当前租户 UUID（或 None）

    仅对已认证用户处理角色，依赖 AuthenticationMiddleware 已设置 request.user。
    实现委托给 ResolveRoleContext（与 JWT 链路共用唯一实现）。
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        ResolveRoleContext(request)
        return self.get_response(request)

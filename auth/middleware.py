import logging
from django.core.exceptions import ObjectDoesNotExist
from gvsdsdk.models import UserRole, Role
from gvsdsdk.model_utils import UUIDObj

logger = logging.getLogger(__name__)


class CurrentRoleMiddleware:
    """解析 CurrentRoleUUID Cookie，设置 request.current_role / current_company_uuid / current_tenant_uuid

    request.current_role        : gvsdsdk.models.Role 实例（或 None）
    request.current_company_uuid: 通过 UserRole.CompanyUUID 获取当前角色所属公司实例（或 None）
    request.current_tenant_uuid : 通过 Company.TenantUUID 获取当前租户 UUID bytes（或 None）

    仅对已认证用户处理角色，依赖 DRF 认证已设置 request.user。
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.current_role = None
        request.current_company_uuid = None
        request.current_tenant_uuid = None

        # 仅对已认证用户处理角色
        if hasattr(request, 'user') and request.user and request.user.is_authenticated:
            role_uuid_str = request.COOKIES.get('CurrentRoleUUID', '')
            if role_uuid_str:
                try:
                    role_uuid_bytes = UUIDObj(role_uuid_str).bytes
                    # gvsdsdk.UserRole 的 CompanyUUID 是 UUIDField（非 ForeignKey），
                    # 不能用 select_related；RoleUUID 同理。
                    user_role = UserRole.objects.filter(
                        UserUUID=request.user.UserUUID,
                        RoleUUID=role_uuid_bytes
                    ).first()
                    if user_role:
                        # 单独查询 Role 实例（Role.TenantUUID 提供租户信息）
                        role = Role.objects.filter(RoleUUID=role_uuid_bytes).first()
                        if role:
                            request.current_role = role
                            # 从 Role 实例获取租户 UUID（bytes）
                            request.current_tenant_uuid = role.TenantUUID
                        # UserRole.CompanyUUID 是 UUIDField，直接赋值 UUID bytes
                        if user_role.CompanyUUID:
                            request.current_company_uuid = user_role.CompanyUUID
                        logger.info('[CurrentRoleMiddleware] cookie=%s, role=%s, tenant=%s, company=%s',
                                    role_uuid_str[:36],
                                    getattr(role, 'RoleName', None) if role else None,
                                    request.current_tenant_uuid,
                                    request.current_company_uuid)
                    else:
                        logger.warning('[CurrentRoleMiddleware] cookie=%s 但 UserRole 未找到 (user=%s)',
                                       role_uuid_str[:36], request.user)
                except (ValueError, KeyError, ObjectDoesNotExist) as e:
                    logger.warning('[CurrentRoleMiddleware] cookie=%s 解析失败: %s', role_uuid_str[:36], e)
                except Exception:
                    logger.exception('CurrentRoleMiddleware 解析当前角色失败')
            else:
                logger.warning('[CurrentRoleMiddleware] 已认证用户 %s 无 CurrentRoleUUID cookie', request.user)
        return self.get_response(request)

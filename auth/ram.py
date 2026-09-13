"""模块：RAM 资源访问管理（Resource Access Management）服务层

RAM 是统一"主体→资源→操作"授权模型（借鉴 AWS RAM/IAM），与现有 RBAC 融合：

    主体 Principal   ：Role（角色）/ User（用户）
    资源 Resource     ：app（应用）/ menu（菜单）/ data_scope（数据权限，预留）
    操作 Action       ：view（可见）/ use（使用）/ manage（管理）/ grant（授权）
    作用域 Scope       ：授权只在归属租户/公司内生效（null=全租户/全公司，仅 super 可授）

与现有权限体系融合：
    - App 可见性  = 安装状态（AppManager.CheckAccess）∩ RAM(角色→App, view)
    - 菜单可见性  = 公司过滤 ∧ 权限码校验 ∧ 应用安装 ∧ RAM(角色→Menu, view)（可选启用）
    - 数据权限暂不接管（RoleScope 四级已实现），预留 ResourceType=data_scope

判定优先级（与现有体系一致）：
    - 管理员（admin:super/tenant/company）→ True（豁免 RAM 校验）
    - 其余按 RAMGrant 命中（GrantStatus=1 + 作用域匹配）
"""
import logging
import uuid as _uuid

from gvsdsdk.models import RAMGrant
from gvsdsdk.model_utils import UUIDObj

logger = logging.getLogger(__name__)

# === 资源类型 ===
RESOURCE_APP = 'app'                 # 应用（AppUUID）
RESOURCE_MENU = 'menu'               # 菜单（MenuUUID）
RESOURCE_DATA_SCOPE = 'data_scope'   # 数据权限（预留，暂不接管）

# === 操作 ===
ACTION_VIEW = 'view'                 # 可见（菜单注入/列表可见）
ACTION_USE = 'use'                   # 使用（打开应用/调用 API）
ACTION_MANAGE = 'manage'             # 管理（安装/配置）
ACTION_GRANT = 'grant'               # 授权（分发 RAM 授权）

# === 主体类型 ===
PRINCIPAL_ROLE = 'role'              # 角色
PRINCIPAL_USER = 'user'              # 用户


def _ToBytes(value):
    """统一转 bytes（兼容 bytes/UUIDObj/uuid.UUID/str）"""
    if value is None:
        return None
    if isinstance(value, bytes):
        return value
    if hasattr(value, 'bytes'):
        return value.bytes
    if isinstance(value, str):
        try:
            return UUIDObj(value).bytes
        except Exception:
            return None
    return None


class RAMManager:
    """RAM 授权统一服务层"""

    # ==================== 授权 ====================

    @staticmethod
    def GrantAccess(PrincipalType, PrincipalUUID, ResourceType, ResourceUUID,
                    Action, TenantUUID=None, CompanyUUID=None, GrantBy=None):
        """授予访问权限（幂等 upsert：已存在同组合则激活，不存在则创建）

        Args:
            PrincipalType: 主体类型（role/user）
            PrincipalUUID: 主体 UUID（角色或用户）
            ResourceType: 资源类型（app/menu/data_scope）
            ResourceUUID: 资源 UUID
            Action: 操作（view/use/manage/grant）
            TenantUUID: 归属租户（None=跨租户授权，仅 super 可用）
            CompanyUUID: 归属公司（None=租户内全部公司）
            GrantBy: 授权人 UUID（可选）

        Returns:
            (grant_record, err)：err 非 None 表示失败
        """
        principal_bytes = _ToBytes(PrincipalUUID)
        resource_bytes = _ToBytes(ResourceUUID)
        tenant_bytes = _ToBytes(TenantUUID)
        company_bytes = _ToBytes(CompanyUUID)
        grant_by_bytes = _ToBytes(GrantBy)
        if not principal_bytes or not resource_bytes:
            return None, '主体/资源 UUID 无效'
        if PrincipalType not in (PRINCIPAL_ROLE, PRINCIPAL_USER):
            return None, f'主体类型无效: {PrincipalType}'
        if ResourceType not in (RESOURCE_APP, RESOURCE_MENU, RESOURCE_DATA_SCOPE):
            return None, f'资源类型无效: {ResourceType}'
        if Action not in (ACTION_VIEW, ACTION_USE, ACTION_MANAGE, ACTION_GRANT):
            return None, f'操作无效: {Action}'

        grant, created = RAMGrant.objects.get_or_create(
            PrincipalType=PrincipalType,
            PrincipalUUID=principal_bytes,
            ResourceType=ResourceType,
            ResourceUUID=resource_bytes,
            Action=Action,
            TenantUUID=tenant_bytes,
            CompanyUUID=company_bytes,
            defaults={
                'GrantUUID': _uuid.uuid4().bytes,
                'GrantStatus': 1,
                'GrantBy': grant_by_bytes,
            },
        )
        if not created:
            if grant.GrantStatus != 1:
                grant.GrantStatus = 1
                if grant_by_bytes:
                    grant.GrantBy = grant_by_bytes
                grant.save(update_fields=['GrantStatus', 'GrantBy'])
            elif grant_by_bytes:
                grant.GrantBy = grant_by_bytes
                grant.save(update_fields=['GrantBy'])
        return grant, ''

    @staticmethod
    def RevokeAccess(GrantUUID):
        """撤销授权（按授权 UUID）"""
        grant = RAMGrant.objects.filter(GrantUUID=_ToBytes(GrantUUID)).first()
        if grant is None:
            return False, '授权记录不存在'
        grant.GrantStatus = 0
        grant.save(update_fields=['GrantStatus'])
        return True, ''

    @staticmethod
    def RevokeByResource(ResourceType, ResourceUUID, Action=None, PrincipalUUID=None):
        """按资源撤销全部/部分授权（如卸载应用时清理 RAM 授权）"""
        qs = RAMGrant.objects.filter(ResourceType=ResourceType, ResourceUUID=_ToBytes(ResourceUUID))
        if Action:
            qs = qs.filter(Action=Action)
        if PrincipalUUID:
            qs = qs.filter(PrincipalUUID=_ToBytes(PrincipalUUID))
        count = qs.update(GrantStatus=0)
        return count

    # ==================== 校验 ====================

    @staticmethod
    def CheckAccess(PrincipalUUID, ResourceType, ResourceUUID, Action,
                    TenantUUID=None, CompanyUUID=None):
        """校验主体是否对资源拥有指定操作（GrantStatus=1 + 作用域匹配）

        作用域规则：
            - 授权 TenantUUID 为 None（跨租户）→ 命中
            - 授权 TenantUUID 等于请求租户 → 继续匹配公司
            - 公司：授权 CompanyUUID 为 None（租户内全公司）→ 命中；等于请求公司 → 命中

        Returns:
            bool
        """
        principal_bytes = _ToBytes(PrincipalUUID)
        resource_bytes = _ToBytes(ResourceUUID)
        tenant_bytes = _ToBytes(TenantUUID)
        company_bytes = _ToBytes(CompanyUUID)
        if not principal_bytes or not resource_bytes:
            return False

        qs = RAMGrant.objects.filter(
            PrincipalUUID=principal_bytes,
            ResourceType=ResourceType,
            ResourceUUID=resource_bytes,
            Action=Action,
            GrantStatus=1,
        )
        if tenant_bytes is not None:
            qs = qs.filter(_TenantScopeQ(tenant_bytes))
        if company_bytes is not None:
            qs = qs.filter(_CompanyScopeQ(company_bytes))
        return qs.exists()

    # ==================== 查询 ====================

    @staticmethod
    def ListGrants(ResourceType=None, ResourceUUID=None, PrincipalType=None,
                   PrincipalUUID=None, Action=None, Status=1, TenantUUID=None,
                   CompanyUUID=None):
        """列出授权记录（灵活过滤，返回 dict 列表）

        Returns:
            list[dict]：{GrantUUID, PrincipalType, PrincipalUUID, ResourceType,
                         ResourceUUID, Action, TenantUUID, CompanyUUID, GrantStatus,
                         GrantBy, GrantTime}
        """
        qs = RAMGrant.objects.all()
        if Status is not None:
            qs = qs.filter(GrantStatus=Status)
        if ResourceType:
            qs = qs.filter(ResourceType=ResourceType)
        if ResourceUUID:
            qs = qs.filter(ResourceUUID=_ToBytes(ResourceUUID))
        if PrincipalType:
            qs = qs.filter(PrincipalType=PrincipalType)
        if PrincipalUUID:
            qs = qs.filter(PrincipalUUID=_ToBytes(PrincipalUUID))
        if Action:
            qs = qs.filter(Action=Action)
        if TenantUUID:
            qs = qs.filter(TenantUUID=_ToBytes(TenantUUID))
        if CompanyUUID:
            qs = qs.filter(CompanyUUID=_ToBytes(CompanyUUID))
        return [RAMManager._ToFlat(g) for g in qs]

    @staticmethod
    def ListResourcesForPrincipal(PrincipalUUID, ResourceType, Action,
                                  TenantUUID=None, CompanyUUID=None):
        """列出主体可访问的资源 UUID 集合（bytes 列表）

        返回满足"作用域匹配 + GrantStatus=1"的资源 UUID bytes 列表，
        供菜单注入/应用可见性批量判定使用（一次查询取集合，避免 N+1）。
        """
        principal_bytes = _ToBytes(PrincipalUUID)
        if not principal_bytes:
            return []
        qs = RAMGrant.objects.filter(
            PrincipalUUID=principal_bytes,
            ResourceType=ResourceType,
            Action=Action,
            GrantStatus=1,
        )
        if TenantUUID is not None:
            qs = qs.filter(_TenantScopeQ(_ToBytes(TenantUUID)))
        if CompanyUUID is not None:
            qs = qs.filter(_CompanyScopeQ(_ToBytes(CompanyUUID)))
        return list(qs.values_list('ResourceUUID', flat=True))

    @staticmethod
    def ListPolicyResourceUUIDs(ResourceType, Action, TenantUUID=None, CompanyUUID=None):
        """列出某类资源/操作下"已配置策略"的资源 UUID 集合（bytes 列表）

        与 ListResourcesForPrincipal 的区别：不限定主体，返回整体策略集合，
        供应用/菜单可见性判定"是否启用了 RAM 策略"（无策略=向后兼容全员可见）。
        """
        qs = RAMGrant.objects.filter(
            ResourceType=ResourceType,
            Action=Action,
            GrantStatus=1,
        )
        if TenantUUID is not None:
            qs = qs.filter(_TenantScopeQ(_ToBytes(TenantUUID)))
        if CompanyUUID is not None:
            qs = qs.filter(_CompanyScopeQ(_ToBytes(CompanyUUID)))
        return list(qs.values_list('ResourceUUID', flat=True))

    # ==================== 内部 ====================

    @staticmethod
    def _ToFlat(grant):
        """RAMGrant 实例 → dict"""
        return {
            'GrantUUID': UUIDObj(grant.GrantUUID).str if grant.GrantUUID else '',
            'PrincipalType': grant.PrincipalType,
            'PrincipalUUID': UUIDObj(grant.PrincipalUUID).str if grant.PrincipalUUID else '',
            'ResourceType': grant.ResourceType,
            'ResourceUUID': UUIDObj(grant.ResourceUUID).str if grant.ResourceUUID else '',
            'Action': grant.Action,
            'TenantUUID': UUIDObj(grant.TenantUUID).str if grant.TenantUUID else '',
            'CompanyUUID': UUIDObj(grant.CompanyUUID).str if grant.CompanyUUID else '',
            'GrantStatus': grant.GrantStatus,
            'GrantBy': UUIDObj(grant.GrantBy).str if grant.GrantBy else '',
            'GrantTime': grant.GrantTime.isoformat() if grant.GrantTime else None,
        }


def _TenantScopeQ(tenant_bytes):
    """租户作用域过滤：授权跨租户（TenantUUID 空）或匹配当前租户"""
    from django.db.models import Q
    return Q(TenantUUID__isnull=True) | Q(TenantUUID=tenant_bytes)


def _CompanyScopeQ(company_bytes):
    """公司作用域过滤：授权租户内全公司（CompanyUUID 空）或匹配当前公司"""
    from django.db.models import Q
    return Q(CompanyUUID__isnull=True) | Q(CompanyUUID=company_bytes)

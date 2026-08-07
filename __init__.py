"""GVSDS SDK — GVSDS 分布式 SDK

GVSDS 分布式服务间通信 SDK，作为子服务器（如 ATestCServer）和主服务器（gvsds）
之间的统一通信桥梁。

设计原则：
    1. 薄封装：直接包装 gvsds.apps.msync 原生层
    2. 纯转发：方法签名 1:1 对应原生层
    3. PascalCase：全部公开 API 使用 PascalCase
    4. 协议兼容：完全兼容 gvsds/apps/msync 现有协议
    5. 惰性加载：仅在访问属性时才 import 原生层（避免 Django 未初始化时报错）

包结构：
    gvsdsdk/
    ├── auth/               # 用户认证与权限模块
    │   ├── crypto.py       # 密码哈希 + AES 加密
    │   ├── middleware.py    # CurrentRoleMiddleware
    │   ├── permissions.py   # RBAC/ABAC 权限
    │   └── managers.py     # RoleManager（UserManager 在 models.py）
    ├── msync/              # MSYNC 服务间通信
    ├── commission/         # 分佣引擎
    │   ├── models.py       # 分佣数据模型
    │   ├── manager.py      # CommissionEngine 编排器
    │   └── strategies/     # 分账策略
    ├── payment/            # 支付网关
    │   ├── models.py       # 支付数据模型
    │   ├── manager.py      # PaymentManager 编排器
    │   └── gateways/       # 支付渠道适配器
    ├── order/              # 订单模板与订单
    │   └── models.py       # 订单数据模型
    ├── models.py           # SAAS 核心数据模型
    ├── model_base.py       # QModel
    ├── model_utils.py      # UUIDObj
    └── fluent.py           # FluentQuery ORM 查询层

使用示例：
    from gvsdsdk import ServiceRegistry, OGMManager, TransactionManager
    from gvsdsdk.auth import PermissionVerifier
    from gvsdsdk.commission import CommissionEngine
    from gvsdsdk.payment import PaymentManager
    print(gvsdsdk.__version__)  # '1.0.0'
"""

import os as _os
import sys as _sys
import platform as _platform
from typing import Optional, Tuple


# === 版本定义（单一来源） ===

#: 主版本号
VERSION_MAJOR = 1
#: 次版本号
VERSION_MINOR = 0
#: 补丁版本号
VERSION_PATCH = 0
#: 预发布标签（如 'a1', 'b2', 'rc1'）
VERSION_PRERELEASE = ''

#: 完整版本字符串（PEP 440 兼容）
__version__ = f'{VERSION_MAJOR}.{VERSION_MINOR}.{VERSION_PATCH}{VERSION_PRERELEASE}'


# === 包元数据 ===

#: 物理安装路径（pkg.__file__ 所在目录）
_PKG_DIR = _os.path.dirname(_os.path.abspath(__file__))


def get_version() -> str:
    """返回 SDK 版本字符串"""
    return __version__


def get_version_info() -> dict:
    """返回详细版本信息字典

    Returns:
        包含 version, location, python_version, platform 等字段
    """
    return {
        'Version': __version__,
        'Major': VERSION_MAJOR,
        'Minor': VERSION_MINOR,
        'Patch': VERSION_PATCH,
        'Prerelease': VERSION_PRERELEASE,
        'Location': _PKG_DIR,
        'Python': _sys.version.split()[0],
        'Platform': _platform.platform(),
        'Machine': _platform.machine(),
    }


def check_min_version(min_version: str) -> Tuple[bool, str]:
    """检查当前版本是否 >= 给定最低版本

    Args:
        min_version: 形如 '1.0.0' 的最低版本字符串

    Returns:
        (is_ok, message) 元组
    """
    def parse(v: str):
        parts = v.split('.')
        major = int(parts[0]) if len(parts) > 0 else 0
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch_str = parts[2] if len(parts) > 2 else '0'
        digits = ''
        for c in patch_str:
            if c.isdigit():
                digits += c
            else:
                break
        patch = int(digits) if digits else 0
        return (major, minor, patch)

    current = parse(__version__)
    required = parse(min_version)

    if current >= required:
        return True, f'OK: SDK {__version__} >= {min_version}'
    return False, f'FAIL: SDK {__version__} < {min_version} (required)'


# === 惰性加载（PEP 562）===

def _load_msync():
    from gvsdsdk import msync as _mod
    return _mod


def _load_auth():
    from gvsdsdk import auth as _mod
    return _mod


def _load_commission():
    from gvsdsdk import commission as _mod
    return _mod


def _load_payment():
    from gvsdsdk import payment as _mod
    return _mod


def _load_order():
    from gvsdsdk import order as _mod
    return _mod


def _load_stats():
    from gvsdsdk import stats as _mod
    return _mod


def _load_models():
    from gvsdsdk import models as _mod
    return _mod


def _load_fluent():
    from gvsdsdk import fluent as _mod
    return _mod


def _load_model_base():
    from gvsdsdk import model_base as _mod
    return _mod


def _load_model_utils():
    from gvsdsdk import model_utils as _mod
    return _mod


_LAZY_SYMBOLS = {
    # ── MSYNC 服务间通信 ──────────────────────────────────
    'SignPayload': ('msync', 'SignPayload'),
    'VerifySignature': ('msync', 'VerifySignature'),
    'GenerateChallenge': ('msync', 'GenerateChallenge'),
    'BuildAuthPayload': ('msync', 'BuildAuthPayload'),
    'SignAuthPayload': ('msync', 'SignAuthPayload'),
    'VerifyAuthPayload': ('msync', 'VerifyAuthPayload'),
    'ServiceTokenEncoder': ('msync', 'ServiceTokenEncoder'),
    'AuthenticateService': ('msync', 'AuthenticateService'),
    'RefreshServiceToken': ('msync', 'RefreshServiceToken'),
    'ValidateAccessToken': ('msync', 'ValidateAccessToken'),
    'IssueServiceToken': ('msync', 'IssueServiceToken'),
    'ServiceRegistry': ('msync', 'ServiceRegistry'),
    'RegistryError': ('msync', 'RegistryError'),
    'OGMManager': ('msync', 'OGMManager'),
    'OGMError': ('msync', 'OGMError'),
    'TransactionManager': ('msync', 'TransactionManager'),
    'TransactionError': ('msync', 'TransactionError'),
    'StepExecutionError': ('msync', 'StepExecutionError'),
    'LocalTransactionManager': ('msync', 'LocalTransactionManager'),
    'StepHandler': ('msync', 'StepHandler'),
    'local_tx': ('msync', 'local_tx'),
    'ServiceClient': ('msync', 'ServiceClient'),
    'ServiceClientError': ('msync', 'ServiceClientError'),

    # ── 用户认证与权限 ────────────────────────────────────
    'GetDefaultAvatar': ('auth', 'GetDefaultAvatar'),
    'HashPassword': ('auth', 'HashPassword'),
    'VerifyPassword': ('auth', 'VerifyPassword'),
    'SymmetricEncrypt': ('auth', 'SymmetricEncrypt'),
    'SymmetricDecrypt': ('auth', 'SymmetricDecrypt'),
    'FileSHA1': ('auth', 'FileSHA1'),
    'FileSHA256': ('auth', 'FileSHA256'),
    'BytesSHA1': ('auth', 'BytesSHA1'),
    'BytesSHA256': ('auth', 'BytesSHA256'),
    'CurrentRoleMiddleware': ('auth', 'CurrentRoleMiddleware'),
    'IsAdminUser': ('auth', 'IsAdminUser'),
    'IsCompanyMember': ('auth', 'IsCompanyMember'),
    'CompanyScopedMixin': ('auth', 'CompanyScopedMixin'),
    'PermissionVerifier': ('auth', 'PermissionVerifier'),
    'RoleManager': ('auth', 'RoleManager'),
    'ShopManager': ('auth', 'ShopManager'),
    'PositionManager': ('auth', 'PositionManager'),
    'AuthManager': ('auth', 'AuthManager'),
    'AdminUserManager': ('auth', 'AdminUserManager'),
    'TenantManager': ('auth', 'TenantManager'),
    'CompanyManager': ('auth', 'CompanyManager'),
    'DepartmentManager': ('auth', 'DepartmentManager'),
    'MessageManager': ('auth', 'MessageManager'),
    'UserService': ('auth', 'UserService'),

    # ── 分佣引擎 ──────────────────────────────────────────
    'CommissionEngine': ('commission', 'CommissionEngine'),
    'CommissionError': ('commission', 'CommissionError'),
    'CommissionEvent': ('commission', 'CommissionEvent'),
    'RateTable': ('commission', 'RateTable'),
    'RateEntry': ('commission', 'RateEntry'),
    'CommissionRule': ('commission', 'CommissionRule'),
    'CommissionRecord': ('commission', 'CommissionRecord'),
    'PreSettlement': ('commission', 'PreSettlement'),
    'CreateStrategy': ('commission', 'CreateStrategy'),
    'PercentageStrategy': ('commission', 'PercentageStrategy'),
    'FixedStrategy': ('commission', 'FixedStrategy'),
    'TieredStrategy': ('commission', 'TieredStrategy'),
    'RemainderStrategy': ('commission', 'RemainderStrategy'),
    'WithdrawalFeeStrategy': ('commission', 'WithdrawalFeeStrategy'),

    # ── 支付网关 ──────────────────────────────────────────
    'PaymentManager': ('payment', 'PaymentManager'),
    'PaymentError': ('payment', 'PaymentError'),
    'PaymentGatewayConfig': ('payment', 'PaymentGatewayConfig'),
    'PaymentTransaction': ('payment', 'PaymentTransaction'),
    'EncryptField': ('payment', 'EncryptField'),
    'PaymentCredentials': ('payment', 'PaymentCredentials'),
    'PaymentRequest': ('payment', 'PaymentRequest'),
    'PaymentResponse': ('payment', 'PaymentResponse'),
    'RefundRequest': ('payment', 'RefundRequest'),
    'RefundResponse': ('payment', 'RefundResponse'),
    'TransferRequest': ('payment', 'TransferRequest'),
    'TransferResponse': ('payment', 'TransferResponse'),
    'QueryResponse': ('payment', 'QueryResponse'),
    'AbstractPaymentGateway': ('payment', 'AbstractPaymentGateway'),
    'GatewayRegistry': ('payment', 'GatewayRegistry'),
    'payment_registry': ('payment', 'payment_registry'),
    'WechatPaymentGateway': ('payment', 'WechatPaymentGateway'),
    'AlipayPaymentGateway': ('payment', 'AlipayPaymentGateway'),
    'BankPaymentGateway': ('payment', 'BankPaymentGateway'),

    # ── 订单模板与订单 ────────────────────────────────────
    'OrderTemplate': ('order', 'OrderTemplate'),
    'OrderTemplateItem': ('order', 'OrderTemplateItem'),
    'OrderLevel': ('order', 'OrderLevel'),
    'Order': ('order', 'Order'),
    'OrderItem': ('order', 'OrderItem'),

    # ── 统计引擎 ──────────────────────────────────────────
    'StatsManager': ('stats', 'StatsManager'),
    'StatMetric': ('stats', 'StatMetric'),
    'StatDimension': ('stats', 'StatDimension'),
    'StatRecord': ('stats', 'StatRecord'),
    'StatRanking': ('stats', 'StatRanking'),
    'StatConfig': ('stats', 'StatConfig'),

    # ── 数据模型 ──────────────────────────────────────────
    'User': ('models', 'User'),
    'Role': ('models', 'Role'),
    'Permission': ('models', 'Permission'),
    'UserRole': ('models', 'UserRole'),
    'UUIDField': ('models', 'UUIDField'),
    'UserManager': ('models', 'UserManager'),
    'User2FA': ('models', 'User2FA'),
    'UserPending2FA': ('models', 'UserPending2FA'),
    'UserAvatar': ('models', 'UserAvatar'),
    'UserLoginLog': ('models', 'UserLoginLog'),
    'AccountActivation': ('models', 'AccountActivation'),
    'RolePermission': ('models', 'RolePermission'),
    'UserABAC': ('models', 'UserABAC'),
    'Password': ('models', 'Password'),
    'Shop': ('models', 'Shop'),
    'RoleShop': ('models', 'RoleShop'),
    'Position': ('models', 'Position'),
    'PositionTemplate': ('models', 'PositionTemplate'),
    'PositionTemplatePermission': ('models', 'PositionTemplatePermission'),
    'Menu': ('models', 'Menu'),
    'WorkOrder': ('models', 'WorkOrder'),
    'AuditLog': ('models', 'AuditLog'),
    'UserRoleDepartment': ('models', 'UserRoleDepartment'),
    'UserRoleWorkgroup': ('models', 'UserRoleWorkgroup'),
    'Message': ('models', 'Message'),
    'UserIDCard': ('models', 'UserIDCard'),
    'PersonalPage': ('models', 'PersonalPage'),
    'UserSettings': ('models', 'UserSettings'),
    'Tenant': ('models', 'Tenant'),
    'Company': ('models', 'Company'),
    'Department': ('models', 'Department'),
    'CompanyInfo': ('models', 'CompanyInfo'),
    'DepartmentSMTM': ('models', 'DepartmentSMTM'),
    'Workgroup': ('models', 'Workgroup'),
    'SimulatedTrigger': ('models', 'SimulatedTrigger'),
    'CashReceiptsPaymentsLedger': ('models', 'CashReceiptsPaymentsLedger'),
    'Asset': ('models', 'Asset'),
    'Liability': ('models', 'Liability'),
    'FixedBinaryField': ('models', 'FixedBinaryField'),
    'QModel': ('model_base', 'QModel'),
    'QModelBase': ('model_base', 'QModelBase'),

    # ── ORM 查询层 ────────────────────────────────────────
    'FluentQuery': ('fluent', 'FluentQuery'),
    'Session': ('fluent', 'Session'),
    'db': ('fluent', 'db'),
    'func': ('fluent', 'func'),
    'FQ': ('fluent', 'FQ'),
    'FieldExpression': ('model_base', 'FieldExpression'),

    # ── 工具 ──────────────────────────────────────────────
    'UUIDObj': ('model_utils', 'UUIDObj'),
}

_LOADERS = {
    'msync': _load_msync,
    'auth': _load_auth,
    'commission': _load_commission,
    'payment': _load_payment,
    'order': _load_order,
    'stats': _load_stats,
    'models': _load_models,
    'fluent': _load_fluent,
    'model_base': _load_model_base,
    'model_utils': _load_model_utils,
}


def __getattr__(name):
    """惰性加载——仅在被访问时才 import 真正的模块"""
    if name in _LAZY_SYMBOLS:
        module_name, attr = _LAZY_SYMBOLS[name]
        mod = _LOADERS[module_name]()
        value = getattr(mod, attr)
        globals()[name] = value
        return value
    raise AttributeError(f"module 'gvsdsdk' has no attribute {name!r}")


__all__ = list(_LAZY_SYMBOLS.keys()) + [
    'get_version',
    'get_version_info',
    'check_min_version',
    '__version__',
    'VERSION_MAJOR',
    'VERSION_MINOR',
    'VERSION_PATCH',
]

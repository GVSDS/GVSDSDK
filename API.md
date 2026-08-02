# GVSDS SDK API 使用手册

> 版本：1.0.0 | 更新日期：2026-06-13

## 目录

- [1. 概述](#1-概述)
- [2. 快速开始](#2-快速开始)
- [3. 包结构](#3-包结构)
- [4. 用户管理 (UserManager)](#4-用户管理-usermanager)
- [5. 角色管理 (RoleManager)](#5-角色管理-rolemanager)
- [6. 商铺管理 (ShopManager)](#6-商铺管理-shopmanager)
- [7. 职务管理 (PositionManager)](#7-职务管理-positionmanager)
- [8. 权限验证 (PermissionVerifier)](#8-权限验证-permissionverifier)
- [9. ELT 认证](#9-elt-认证)
- [10. 分佣引擎 (CommissionEngine)](#10-分佣引擎-commissionengine)
- [11. 支付网关 (PaymentManager)](#11-支付网关-paymentmanager)
- [12. MSYNC 服务间通信](#12-msync-服务间通信)
- [13. FluentQuery ORM 查询](#13-fluentquery-orm-查询)
- [14. 数据库路由与建表](#14-数据库路由与建表)
- [15. 数据模型一览](#15-数据模型一览)

---

## 1. 概述

GVSDS SDK 是 GVSDS 分布式服务 SDK，提供：

- **SAAS 用户管理**：用户、角色、权限、商铺、职务的完整 CRUD 和关联管理
- **ELT 认证**：Cookie → EphemeralToken → User 的无状态认证体系
- **分佣引擎**：5 种分账策略 + 预结算 + 邀请链分佣
- **支付网关**：微信/支付宝/银行卡三渠道适配 + 统一编排
- **MSYNC 通信**：服务注册/发现/OGM/分布式事务
- **FluentQuery**：SQLAlchemy 风格的 ORM 查询层

全程 PascalCase 命名，从数据库到前端无中间转换层。

---

## 2. 快速开始

### 安装

```bash
pip install -e /path/to/gvsdsdk
```

### settings.py 配置

```python
INSTALLED_APPS = [
    ...
    'gvsdsdk',
]

# 方式一：子服务器连接 SAAS 主库（只读）
DATABASES = {
    'default': { ... },   # 本地数据库
    'saas': {             # SAAS 主库
        'ENGINE': 'django.db.backends.mysql',
        'NAME': 'Users',
        'USER': 'root',
        'PASSWORD': '***',
        'HOST': 'saas-db.gvsds.com',
        'PORT': '3306',
    },
}
DATABASE_ROUTERS = ['gvsdsdk.db_router.SAASDatabaseRouter']
GVSDSDK_SAAS_READ_ONLY = True  # 默认 True

# 方式二：同源数据库（可读写）
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': 'mydb',
        ...
    },
}
DATABASE_ROUTERS = []
GVSDSDK_MANAGE_TABLES = True  # 允许 Django 管理表
```

### 建表

```bash
# 方式一：Django migrate（需 GVSDSDK_MANAGE_TABLES=True）
python manage.py makemigrations gvsdsdk
python manage.py migrate

# 方式二：gvsdsdk_init 命令（无需 GVSDSDK_MANAGE_TABLES）
python manage.py gvsdsdk_init --db default
python manage.py gvsdsdk_init --db default --dry-run     # 仅打印 SQL
python manage.py gvsdsdk_init --db default --modules auth,commission,payment
```

---

## 3. 包结构

```
gvsdsdk/
├── __init__.py          # 统一入口（惰性加载 90+ 符号）
├── models.py            # SAAS 核心数据模型（17 张表）
├── model_base.py        # QModel 元类 + FluentQuery 注入
├── model_utils.py       # UUIDObj
├── fluent.py            # FluentQuery ORM 查询层
├── db_router.py         # SAASDatabaseRouter
├── apps.py              # Django AppConfig
├── management/
│   └── commands/
│       └── gvsdsdk_init.py  # 建表命令
│
├── auth/                # 用户认证模块
│   ├── cookie.py        # Cookie 管理
│   ├── crypto.py        # 密码哈希 + AES-256-GCM
│   ├── jwt_auth.py      # JWT 认证后端
│   ├── middleware.py     # CurrentRoleMiddleware
│   ├── permissions.py    # RBAC/ABAC 权限
│   └── managers.py      # UserManager + RoleManager + ShopManager + PositionManager
│
├── msync/               # MSYNC 服务间通信
│   ├── auth.py          # 服务认证签名
│   ├── registry.py      # 服务注册中心
│   ├── ogm.py           # 全局对象映射
│   ├── Transaction.py   # 分布式事务
│   ├── LocalTx.py       # 本地事务
│   └── client.py        # 服务客户端
│
├── commission/          # 分佣引擎
│   ├── models.py        # 6 张数据模型
│   ├── manager.py       # CommissionEngine 编排器
│   └── strategies/
│       └── engine.py    # 5 种策略 + 工厂
│
└── payment/             # 支付网关
    ├── models.py        # 2 张数据模型
    ├── manager.py       # PaymentManager 编排器
    └── gateways/
        ├── base.py      # 抽象网关 + 数据类
        ├── registry.py  # GatewayRegistry + payment_registry
        ├── wechat.py    # 微信支付适配器
        ├── alipay.py    # 支付宝适配器
        └── bank.py      # 银行卡适配器
```

---

## 4. 用户管理 (UserManager)

```python
from gvsdsdk.models import User

# 创建用户
user = User.objects.create_user(username='alice', password='xxx', email='a@b.com')
user = User.objects.create_superuser(username='admin', password='xxx')

# 密码
user.SetPassword('new_pass')       # bcrypt 哈希
user.CheckPassword('input')        # 验证密码

# 查询
user = User.objects.get(UserName='alice')
users = User.objects.filter(UserAccountLicense=1)

# 属性
user.IsActive          # UserAccountLicense == 1
user.IsOnJob           # UserPositionStatus == 0
user.UUIDStr           # UUID 字符串
user.HasPermission('menu:admin')  # 检查权限
```

---

## 5. 角色管理 (RoleManager)

```python
from gvsdsdk.auth.managers import RoleManager

# 创建角色
role = RoleManager.CreateRole(name='管理员', company_uuid=company_uuid)

# 分配/移除角色
RoleManager.AssignRole(user_uuid=user.UserUUID, role_uuid=role.RoleUUID)
RoleManager.RemoveRole(user_uuid=user.UserUUID, role_uuid=role.RoleUUID)

# 查询角色
roles = RoleManager.GetRolesForUser(user_uuid=user.UserUUID)
users = RoleManager.GetUsersForRole(role_uuid=role.RoleUUID)

# 分配/移除权限
RoleManager.AssignPermission(role_uuid=role.RoleUUID, perm_code='menu:admin', perm_name='菜单管理')
RoleManager.RemovePermission(role_uuid=role.RoleUUID, perm_code='menu:admin')

# 查询权限
perms = RoleManager.GetPermissionsForRole(role_uuid=role.RoleUUID)
```

---

## 6. 商铺管理 (ShopManager)

```python
from gvsdsdk.auth.managers import ShopManager

# 创建商铺
shop = ShopManager.CreateShop(
    name='旗舰店',
    company_uuid=company_uuid,
    code='SHOP-001',
    shop_type=1,
    ShopAddress='北京市朝阳区xxx',
    ShopContact='张三',
    ShopPhone='13800138000',
    ShopDesc='公司旗舰门店',
)

# 角色关联商铺
ShopManager.AssignRole(role_uuid=role.RoleUUID, shop_uuid=shop.ShopUUID, is_main_shop=True)
ShopManager.RemoveRole(role_uuid=role.RoleUUID, shop_uuid=shop.ShopUUID)

# 查询
shops = ShopManager.GetShopsForRole(role_uuid=role.RoleUUID)       # 角色的所有商铺
roles = ShopManager.GetRolesForShop(shop_uuid=shop.ShopUUID)       # 商铺的所有角色
main_shop = ShopManager.GetMainShopForRole(role_uuid=role.RoleUUID) # 角色的主商铺
company_shops = ShopManager.GetShopsForCompany(company_uuid=company_uuid) # 公司的所有商铺

# 模型方法
shop.GetRoles()  # 商铺关联的所有角色
```

### 商铺模型字段

| 字段 | 类型 | 说明 |
|------|------|------|
| ShopUUID | BINARY(16) PK | 商铺 UUID |
| CompanyUUID | BINARY(16) | 所属公司 UUID |
| ShopName | VARCHAR(128) | 商铺名称 |
| ShopCode | VARCHAR(64) | 商铺编码 |
| ShopType | SMALLINT | 商铺类型 |
| ShopStatus | SMALLINT | 状态（1=启用） |
| ShopAddress | VARCHAR(512) | 地址 |
| ShopContact | VARCHAR(128) | 联系人 |
| ShopPhone | VARCHAR(32) | 联系电话 |
| ShopDesc | VARCHAR(512) | 描述 |
| CreateTime | DATETIME | 创建时间 |
| UpdateTime | DATETIME | 更新时间 |

### 角色商铺关联表 (RoleShop)

| 字段 | 类型 | 说明 |
|------|------|------|
| RoleShopUUID | BINARY(16) PK | 关联 UUID |
| RoleUUID | BINARY(16) | 角色 UUID |
| ShopUUID | BINARY(16) | 商铺 UUID |
| IsMainShop | SMALLINT | 是否主商铺（0/1） |
| CreateTime | DATETIME | 创建时间 |

---

## 7. 职务管理 (PositionManager)

```python
from gvsdsdk.auth.managers import PositionManager

# 创建职务
position = PositionManager.CreatePosition(
    name='总经理',
    company_uuid=company_uuid,
    code='POS-CEO',
    level=10,
    PositionDesc='公司最高管理职务',
)

# 角色关联职务
PositionManager.AssignRole(role_uuid=role.RoleUUID, position_uuid=position.PositionUUID, is_main_position=True)
PositionManager.RemoveRole(role_uuid=role.RoleUUID, position_uuid=position.PositionUUID)

# 查询
positions = PositionManager.GetPositionsForRole(role_uuid=role.RoleUUID)       # 角色的所有职务
roles = PositionManager.GetRolesForPosition(position_uuid=position.PositionUUID) # 职务的所有角色
main_pos = PositionManager.GetMainPositionForRole(role_uuid=role.RoleUUID)     # 角色的主职务
company_positions = PositionManager.GetPositionsForCompany(company_uuid=company_uuid) # 公司的所有职务

# 模型方法
position.GetRoles()  # 职务关联的所有角色
```

### 职务模型字段

| 字段 | 类型 | 说明 |
|------|------|------|
| PositionUUID | BINARY(16) PK | 职务 UUID |
| CompanyUUID | BINARY(16) | 所属公司 UUID |
| PositionName | VARCHAR(128) | 职务名称 |
| PositionCode | VARCHAR(64) | 职务编码 |
| PositionLevel | SMALLINT | 职务级别 |
| PositionStatus | SMALLINT | 状态（1=启用） |
| PositionDesc | VARCHAR(512) | 描述 |
| CreateTime | DATETIME | 创建时间 |
| UpdateTime | DATETIME | 更新时间 |

### 角色职务关联表 (RolePosition)

| 字段 | 类型 | 说明 |
|------|------|------|
| RolePositionUUID | BINARY(16) PK | 关联 UUID |
| RoleUUID | BINARY(16) | 角色 UUID |
| PositionUUID | BINARY(16) | 职务 UUID |
| IsMainPosition | SMALLINT | 是否主职务（0/1） |
| CreateTime | DATETIME | 创建时间 |

---

## 8. 权限验证 (PermissionVerifier)

```python
from gvsdsdk.auth.permissions import PermissionVerifier, IsAdminUser, CompanyScopedMixin

# 手动验证
if PermissionVerifier(perm_level=1, perm_attrs=['menu:admin'], user=request.user, request=request):
    pass

# DRF 权限类
class MyViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsAdminUser]

# 公司范围自动过滤
class MyCompanyViewSet(CompanyScopedMixin, viewsets.ModelViewSet):
    company_field = 'CompanyUUID'  # 自动按当前角色公司过滤
```

---

## 9. ELT 认证

### 认证流程

```
浏览器 Cookie (access_token/refresh_token) → JWTAuthentication → User
```

### 中间件

```python
# settings.py MIDDLEWARE
'gvsdsdk.auth.middleware.CurrentRoleMiddleware',    # 注入 request.current_role

# 视图中使用
request.user                    # gvsdsdk.models.User 实例
request.current_role            # gvsdsdk.models.Role 实例（或 None）
request.current_company_uuid    # 当前角色所属公司 UUID（或 None）
```

### Cookie 管理

```python
from gvsdsdk.auth.cookie import SetELTCookie, DeleteELTCookie, ELT_COOKIE_NAME

# 登录成功后设置 Cookie
SetELTCookie(response, token_value)

# 登出时删除 Cookie
DeleteELTCookie(response)
```

### 加密工具

```python
from gvsdsdk.auth.crypto import HashPassword, VerifyPassword, SymmetricEncrypt, SymmetricDecrypt

# 密码
hashed = HashPassword('plaintext')
is_valid = VerifyPassword('plaintext', hashed)

# AES-256-GCM 对称加密
encrypted = SymmetricEncrypt('sensitive data', key)
decrypted = SymmetricDecrypt(encrypted, key)
```

---

## 10. 分佣引擎 (CommissionEngine)

```python
from gvsdsdk.commission import CommissionEngine, CommissionError

engine = CommissionEngine()

# 配置规则
engine.ConfigureRule(
    event_type='order_complete',
    participant_rules=[
        {'Type': 'percentage', 'Rate': 0.10, 'ParticipantID': 'platform'},
        {'Type': 'fixed', 'Amount': 50.00, 'ParticipantID': 'agent'},
        {'Type': 'remainder', 'ParticipantID': 'merchant'},
    ],
)

# 触发分佣
engine.Dispatch(
    event_type='order_complete',
    event_key='ORDER-12345',
    total_amount=1000.00,
    tenant_uuid=tenant_uuid,
)

# 邀请链分佣
engine.BuildInviteChainCommission(
    event_type='member_purchase',
    event_key='MEMBER-67890',
    total_amount=500.00,
    invite_chain=[user_uuid_1, user_uuid_2, user_uuid_3],
    tenant_uuid=tenant_uuid,
)

# 预结算
engine.PreSettle(event_key='ORDER-12345', participant_id='agent', amount=50.00)
engine.ConfirmPreSettlement(presettlement_uuid=uuid)
engine.RejectPreSettlement(presettlement_uuid=uuid)

# 查询
records = engine.QueryRecords(participant_id='agent')
total = engine.TotalByParticipant(participant_id='agent')
```

### 策略类型

| 策略 | Type | 说明 |
|------|------|------|
| PercentageStrategy | `percentage` | 比例分账，支持保底/封顶 |
| FixedStrategy | `fixed` | 固定金额 |
| TieredStrategy | `tiered` | 阶梯分账 |
| RemainderStrategy | `remainder` | 剩余归平台 |
| WithdrawalFeeStrategy | `withdrawal_fee` | 提现手续费 |

---

## 11. 支付网关 (PaymentManager)

```python
from gvsdsdk.payment import PaymentManager, PaymentError

manager = PaymentManager()

# 配置网关
manager.ConfigureGateway(
    tenant_uuid=tenant_uuid,
    platform='wechat',
    app_id='wx1234567890',
    merchant_id='1234567890',
    api_key='***',
)

# 发起支付
response = manager.Pay(
    tenant_uuid=tenant_uuid,
    platform='wechat',
    out_trade_no='ORDER-12345',
    total_amount=100.00,
    subject='商品名称',
    pay_method='JSAPI',
    openid='user_openid',
)

# 查询订单
result = manager.Query(tenant_uuid=tenant_uuid, out_trade_no='ORDER-12345')

# 退款
result = manager.Refund(
    tenant_uuid=tenant_uuid,
    out_trade_no='ORDER-12345',
    refund_amount=50.00,
    refund_reason='用户申请退款',
)

# 企业转账
result = manager.Transfer(
    tenant_uuid=tenant_uuid,
    platform='wechat',
    out_trade_no='TRANSFER-001',
    amount=100.00,
    transfer_type='balance',
    openid='user_openid',
)

# 处理回调
result = manager.HandleNotify(platform='wechat', raw_body=request.body)

# 列出可用渠道
channels = manager.ListChannels()
```

### 支付渠道

| 渠道 | Platform | 支付方式 | 转账 |
|------|----------|---------|------|
| 微信支付 | `wechat` | JSAPI/Native/H5/小程序 | 支持 |
| 支付宝 | `alipay` | APP/H5/Native | 支持 |
| 银行卡 | `bank` | APP/H5/Native | 仅 bank 类型 |

---

## 12. MSYNC 服务间通信

```python
from gvsdsdk.msync import ServiceRegistry, OGMManager, TransactionManager, ServiceClient
from gvsdsdk.msync.auth import SignPayload, BuildAuthPayload

# 服务注册
registry = ServiceRegistry()
registry.Register(
    service_name='my-service',
    domain='https://my-service.gvsds.com',
    secret='***',
    scope=['ogm:read', 'ogm:write'],
)

# OGM 全局对象映射
ogm = OGMManager()
mapping_id = ogm.CreateMapping(local_type='User', local_id=user_id)
remote_obj = ogm.GetRemote(mapping_id)

# 分布式事务
tx_mgr = TransactionManager()
tx = tx_mgr.Begin(participants=['service-a', 'service-b'])
tx_mgr.ExecuteStep(tx, 'service-a', 'create_order', {'item': 'xxx'})
tx_mgr.ExecuteStep(tx, 'service-b', 'deduct_stock', {'item': 'xxx'})
tx_mgr.Commit(tx)

# 服务客户端
client = ServiceClient(service_name='target-service')
response = client.Call('/api/resource', method='GET')
```

---

## 13. FluentQuery ORM 查询

```python
from gvsdsdk.models import User, Role, Shop, Position

# 通过 .query 属性使用 FluentQuery
users = User.query.filter(User.UserAccountLicense == 1).all()
admins = User.query.filter(User.IsSuperuser == True).all()

# 运算符重载
young = User.query.filter(User.UserAccountLicense != 0).all()

# 排序和分页
users = User.query.filter(User.IsActive == True).order_by(User.UserCreateTime).limit(10)

# Django ORM 原生查询（始终可用）
users = User.objects.filter(UserAccountLicense=1)
users = User.objects.filter(UserName__contains='admin')
```

---

## 14. 数据库路由与建表

### SAASDatabaseRouter

```python
# settings.py — 子服务器连接 SAAS 主库
DATABASE_ROUTERS = ['gvsdsdk.db_router.SAASDatabaseRouter']

# 可选配置
GVSDSDK_SAAS_DB_ALIAS = 'saas'       # SAAS 数据库别名，默认 'saas'
GVSDSDK_SAAS_READ_ONLY = True         # 子服务器只读，默认 True
```

### gvsdsdk_init 建表命令

```bash
# 完整建表
python manage.py gvsdsdk_init --db default

# 仅打印 SQL
python manage.py gvsdsdk_init --db default --dry-run

# 按模块建表
python manage.py gvsdsdk_init --db default --modules auth
python manage.py gvsdsdk_init --db default --modules auth,commission,payment
```

### GVSDSDK_MANAGE_TABLES

```python
# settings.py — 同源数据库，允许 Django 管理表
GVSDSDK_MANAGE_TABLES = True

# 然后执行
# python manage.py makemigrations gvsdsdk
# python manage.py migrate
```

---

## 15. 数据模型一览

### SAAS 核心模型 (gvsdsdk.models)

| 模型 | 表名 | 说明 |
|------|------|------|
| User | User | 用户表 |
| Role | Role | 角色表 |
| Permission | Permission | 权限表 |
| UserRole | UserRole | 用户-角色多对多 |
| RoleDept | RoleDept | 角色-部门多对多 |
| Shop | Shop | 商铺表 |
| RoleShop | RoleShop | 角色-商铺多对多 |
| Position | Position | 职务表 |
| RolePosition | RolePosition | 角色-职务多对多 |
| EphemeralToken | EphemeralToken | ELT 令牌表 |
| User2FA | User2FA | 双因素认证表 |
| UserPending2FA | UserPending2FA | 2FA 待验证表 |
| UserAvatar | UserAvatar | 用户头像表 |
| UserLoginLog | UserLoginLog | 登录日志表 |
| AccountActivation | AccountActivation | 账户激活码表 |
| UserABAC | UserABAC | 用户 ABAC 表 |
| Password | Password | 密码表 |

### 分佣模型 (gvsdsdk.commission.models)

| 模型 | 表名 | 说明 |
|------|------|------|
| CommissionEvent | CommissionEvent | 分佣事件表 |
| RateTable | CommissionRateTable | 费率表 |
| RateEntry | CommissionRateEntry | 费率条目表 |
| CommissionRule | CommissionRule | 分佣规则表 |
| CommissionRecord | CommissionRecord | 分佣记录表 |
| PreSettlement | CommissionPreSettlement | 预结算表 |

### 支付模型 (gvsdsdk.payment.models)

| 模型 | 表名 | 说明 |
|------|------|------|
| PaymentGatewayConfig | PaymentGatewayConfig | 支付网关配置表 |
| PaymentTransaction | PaymentTransaction | 支付交易流水表 |

---

## 导入速查表

| 用途 | 导入路径 | 顶层快捷 |
|------|---------|---------|
| 用户模型 | `from gvsdsdk.models import User` | `from gvsdsdk import User` |
| 角色管理 | `from gvsdsdk.auth.managers import RoleManager` | `from gvsdsdk import RoleManager` |
| 商铺管理 | `from gvsdsdk.auth.managers import ShopManager` | `from gvsdsdk import ShopManager` |
| 职务管理 | `from gvsdsdk.auth.managers import PositionManager` | `from gvsdsdk import PositionManager` |
| 权限验证 | `from gvsdsdk.auth.permissions import PermissionVerifier` | `from gvsdsdk import PermissionVerifier` |
| JWT 认证 | `from gvsdsdk.auth.jwt_auth import JWTAuthentication` | `from gvsdsdk import JWTAuthentication` |
| Cookie | `from gvsdsdk.auth.cookie import SetELTCookie` | `from gvsdsdk import SetELTCookie` |
| 加密 | `from gvsdsdk.auth.crypto import SymmetricEncrypt` | `from gvsdsdk import SymmetricEncrypt` |
| 中间件 | `from gvsdsdk.auth.middleware import CurrentRoleMiddleware` | `from gvsdsdk import CurrentRoleMiddleware` |
| 分佣引擎 | `from gvsdsdk.commission import CommissionEngine` | `from gvsdsdk import CommissionEngine` |
| 支付网关 | `from gvsdsdk.payment import PaymentManager` | `from gvsdsdk import PaymentManager` |
| 微信支付 | `from gvsdsdk.payment.gateways.wechat import WechatPaymentGateway` | `from gvsdsdk import WechatPaymentGateway` |
| MSYNC 认证 | `from gvsdsdk.msync.auth import SignPayload` | `from gvsdsdk import SignPayload` |
| 服务注册 | `from gvsdsdk.msync import ServiceRegistry` | `from gvsdsdk import ServiceRegistry` |
| OGM | `from gvsdsdk.msync import OGMManager` | `from gvsdsdk import OGMManager` |
| 事务 | `from gvsdsdk.msync import TransactionManager` | `from gvsdsdk import TransactionManager` |
| 数据库路由 | `from gvsdsdk.db_router import SAASDatabaseRouter` | — |
| UUID 工具 | `from gvsdsdk.model_utils import UUIDObj` | `from gvsdsdk import UUIDObj` |
| QModel | `from gvsdsdk.model_base import QModel` | `from gvsdsdk import QModel` |

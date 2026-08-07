# GVSDS - SDK

> **GVSDS 分布式 SDK**：为 GVSDS 平台子服务器（如 `ATestCServer`）和主服务器（`gvsds`）提供共享的协议契约、ORM 模型、权限体系与通信引擎。
>
> **混合实现策略**：
>   - **自有实现**（无 Django 依赖，子服务器开箱即用）：`msync.auth`（HMAC 签名 + `ServiceTokenEncoder`）、`msync.client`（REST 客户端）、`msync.local_tx`（本地事务）、`fluent`（FluentQuery ORM）、`models`（SAAS 数据模型）、`form_service`（通用 CRUD 引擎）
>   - **转发自主服务器**（依赖 Django ORM，仅主服务器可加载）：`msync.auth` 中的 `AuthenticateService` / `RefreshServiceToken` / `ValidateAccessToken` / `IssueServiceToken`、`msync.registry`、`msync.ogm`、`msync.transaction`
>   - **完整业务模块**：`auth`（用户认证/权限）、`commission`（分佣引擎）、`payment`（支付网关）、`order`（订单）、`stats`（统计引擎）
>
> **版本兼容**：主从两端必须使用同一 SDK 版本（当前 `1.0.0`）。

---

## 1. 简介

`gvsdsdk` 是 GVSDS 平台的官方 SDK，提供共享的协议契约、ORM 模型、权限体系与通信引擎。

**子服务器可独立使用**（不依赖 GVSDS 主服务器代码）：

- **用户认证与权限**（`gvsdsdk.auth`）：密码哈希、AES 加密、文件/字节摘要、CurrentRoleMiddleware、RBAC/ABAC 权限、RoleManager / ShopManager / PositionManager / AuthManager / AdminUserManager / TenantManager / CompanyManager / DepartmentManager / MessageManager / MenuManager / WorkOrderManager / InventoryManager / AssetManager / AuditManager / UserService
- **MSYNC 认证**（`gvsdsdk.msync.auth`）：HMAC-SHA256 签名 / 验签、`ServiceTokenEncoder` 为自有实现；`AuthenticateService` / `IssueServiceToken` / `ValidateAccessToken` / `RefreshServiceToken` 转发自主服务器
- **分布式通信**（`gvsdsdk.msync`）：签名 / 客户端 / 本地事务为自有实现；服务注册 / OGM / Saga 事务转发自主服务器
- **数据模型**（`gvsdsdk.models`）：User / Role / Permission / UserRole / Position / Menu / WorkOrder / AuditLog / ItemReference / Backpack / FileAsset 等 SAAS 核心表（managed=False）
- **ORM 查询层**（`gvsdsdk.fluent`）：FluentQuery 链式查询
- **CRUD 引擎**（`gvsdsdk.form_service`）：FormService 通用增改删查 API
- **业务模块**：`commission`（分佣）/ `payment`（支付）/ `order`（订单）/ `stats`（统计）

**主服务器**（`gvsds`）：通过 `gvsdsdk` 与子服务器共享同一份 API 契约，同时直接 import `gvsds.apps.msync` 内部模块。

**MSYNC 模块分类**（按依赖关系）：

| SDK 模块 | 实现方式 | 提供的类/函数 | 是否需 Django |
|---|---|---|---|
| `gvsdsdk.msync.auth`（自有部分） | **自有实现** | `SignPayload`, `VerifySignature`, `GenerateChallenge`, `BuildAuthPayload`, `SignAuthPayload`, `VerifyAuthPayload`, `ServiceTokenEncoder` | 否 |
| `gvsdsdk.msync.auth`（转发部分） | **转发**自 `gvsds.apps.msync.auth` | `AuthenticateService`, `RefreshServiceToken`, `ValidateAccessToken`, `IssueServiceToken` | 是 |
| `gvsdsdk.msync.client` | **自有实现** | `ServiceClient`, `ServiceClientError` | 否 |
| `gvsdsdk.msync.local_tx` | **自有实现** | `LocalTransactionManager`, `StepHandler`, `local_tx` | 否 |
| `gvsdsdk.msync.registry` | **转发**自 `gvsds.apps.msync.registry` | `ServiceRegistry`, `RegistryError` | 是 |
| `gvsdsdk.msync.ogm` | **转发**自 `gvsds.apps.msync.ogm` | `OGMManager`, `OGMError` | 是 |
| `gvsdsdk.msync.transaction` | **转发**自 `gvsds.apps.msync.transaction` | `TransactionManager`, `TransactionError`, `StepExecutionError` | 是 |

**转发模块**的类在 SDK 与原生层是**同一个对象**（`is` 比较为 `True`）；**自有实现**的类是独立代码。

## 2. 安装

子服务器（如 ATestCServer）项目根目录的 `manage.py` 已自动添加 `gvsdsdk` 的父目录到 `sys.path`：

```python
# manage.py:5
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
```

无需额外配置。

**独立使用**（非 GVSDS 生态项目）：

```bash
$env:PYTHONPATH = "D:\SMANAGE\gvsdsdk"
# 或 Linux/macOS
export PYTHONPATH=/path/to/SMANAGE/gvsdsdk
```

依赖：
- **核心**：`gvsdsdk` 自带 ORM 模型（`models.py`）、数据库路由（`db_router.py`）、CRUD 引擎（`form_service.py`）
- **Django**（仅在主服务器场景）：`msync.registry` / `msync.ogm` / `msync.transaction` 三个模块依赖 Django ORM

## 3. 目录结构

```
gvsdsdk/
├── __init__.py         # 惰性加载入口（_LAZY_SYMBOLS 字典 + __getattr__）
├── apps.py             # Django AppConfig
├── db_router.py        # SAASDatabaseRouter
├── form_service.py     # FormService 通用 CRUD 引擎
├── fluent.py           # FluentQuery 链式 ORM 查询
├── model_base.py       # QModel / QModelBase
├── model_utils.py      # UUIDObj
├── models.py           # SAAS 核心数据模型（User/Role/Permission 等）
│
├── msync/              # MSYNC 服务间通信子包
│   ├── __init__.py     # 模块导出
│   ├── auth.py         # HMAC-SHA256 签名 + ServiceTokenEncoder（自有实现，7 个函数）
│   ├── client.py       # REST 微服务客户端（自有实现）
│   ├── local_tx.py     # 本地事务步骤（自有实现）
│   ├── orm_handler.py  # ORM 处理器
│   ├── ogm.py          # OGM（转发自 gvsds.apps.msync.ogm）
│   ├── registry.py     # 服务注册/发现（转发自 gvsds.apps.msync.registry）
│   └── transaction.py  # Saga 分布式事务（转发自 gvsds.apps.msync.transaction）
│
├── auth/               # 用户认证与权限子包
│   ├── __init__.py
│   ├── admin_service.py    # IsAdmin / IsSuperAdmin / IsTenantAdmin
│   ├── cookie.py
│   ├── crypto.py
│   ├── jwt_auth.py
│   ├── managers.py
│   ├── middleware.py       # CurrentRoleMiddleware
│   └── permissions.py      # RBAC/ABAC 权限
│
├── commission/         # 分佣引擎
├── payment/            # 支付网关
├── order/              # 订单模板与订单
├── stats/              # 统计引擎
├── management/         # Django management 命令
└── migrations/         # Django 数据迁移（gvsdsdk 自身表的）
```

**转发模块**（`msync.registry` / `msync.ogm` / `msync.transaction`）每个文件仅 15-17 行，是 `from gvsds.apps.msync.X import Y` 的纯转发；`msync.auth` 中 `AuthenticateService` 等 4 个函数通过 `msync/__init__.py` 的 `__getattr__` 转发，无独立文件。**其他模块**都是完整业务实现。

## 4. 核心 API（PascalCase）

### 4.1 MSYNC 认证 (gvsdsdk.msync.auth)

> 下表前 7 项为**自有实现**（子服务器可用，无 Django 依赖）；后 4 项为**转发**自 `gvsds.apps.msync.auth`（仅主服务器可用）。

| 函数 / 类 | 描述 |
|---|---|
| `SignPayload(payload, secret)` | HMAC-SHA256 签名 |
| `VerifySignature(payload, signature, secret)` | 验签 |
| `GenerateChallenge()` | 生成挑战字符串 |
| `BuildAuthPayload(service, challenge, ts=None)` | 构造认证载荷 |
| `SignAuthPayload(payload, secret)` | 一站式签名 |
| `VerifyAuthPayload(payload, signature, secret)` | 一站式验签 |
| `ServiceTokenEncoder` | JWT 风格令牌编解码 |
| `IssueServiceToken(service_node, ...)` | 签发令牌 |
| `AuthenticateService(service_name, signed_payload, signature)` | 完整认证 |
| `ValidateAccessToken(access_token)` | 校验访问令牌 |
| `RefreshServiceToken(refresh_token)` | 刷新令牌 |

### 4.2 注册中心 (gvsdsdk.registry)

```python
from gvsdsdk import ServiceRegistry

node = ServiceRegistry.Register(
    Name='my-service',
    Domain='http://127.0.0.1:8080',
    Secret='shared-secret',
    Scope=['ogm:read', 'ogm:write'],
    ServiceType=0,                       # 0=Worker, 1=Master
    Metadata={'region': 'cn-east'},
)

ServiceRegistry.Heartbeat('my-service')
ServiceRegistry.Discover('my-service')
ServiceRegistry.DiscoverAll(ActiveOnly=True)
ServiceRegistry.UpdateScope('my-service', ['ogm:read'])
ServiceRegistry.RotateSecret('my-service', 'new-secret')
ServiceRegistry.Deregister('my-service')
```

### 4.3 OGM (gvsdsdk.ogm)

```python
from gvsdsdk import OGMManager

entry = OGMManager.Register(
    ObjectType='user',
    LocalID='123',
    ServiceName='my-service',
    GlobalID=None,                       # 可选，自动生成
    Metadata={'username': 'tom'},
)

info = OGMManager.Resolve(entry['GlobalID'])
local = OGMManager.ResolveLocal('user', '123', 'my-service')
by_type = OGMManager.QueryByType('user')
by_svc = OGMManager.QueryByService('my-service')
OGMManager.Update(entry['GlobalID'], metadata={'vip': True})
OGMManager.Deregister(entry['GlobalID'])
```

### 4.4 分布式事务 (gvsdsdk.transaction)

```python
from gvsdsdk import TransactionManager

dtx = TransactionManager.Create(
    Name='create-order',
    StepsData=[
        {'ServiceName': 'svc-a', 'StepName': 'create_user',  'Payload': {...}},
        {'ServiceName': 'svc-b', 'StepName': 'create_wallet', 'Payload': {...}},
    ],
    Description='创建订单事务',
    TransactionType='saga',
    InitiatorServiceName='my-service',
    TimeoutSeconds=300,
)

TransactionManager.Execute(dtx.TransactionCode, access_token=token)
TransactionManager.Compensate(dtx.TransactionCode)
TransactionManager.Cancel(dtx.TransactionCode)
TransactionManager.GetDetail(dtx.TransactionCode)
TransactionManager.RetryStep(dtx.TransactionCode, step_index=0)
TransactionManager.CheckTimeouts()
TransactionManager.QueryByStatus(status=2)  # Committed
```

### 4.5 本地事务 (gvsdsdk.local_tx)

```python
from gvsdsdk import local_tx

def create_user(payload):
    user_id = do_something(payload)
    return {'UserID': user_id}

def delete_user(payload, result):
    undo_something(result['UserID'])

local_tx.register('create_user', action=create_user, compensate=delete_user)
```

### 4.6 服务客户端 (gvsdsdk.client)

```python
from gvsdsdk import ServiceClient

client = ServiceClient(
    ServiceName='my-service',
    ServiceSecret='shared-secret',
    MasterURL='https://api.gvsds.com',
)

client.Authenticate()
info = client.DiscoverService('gerp')
ogm = client.ResolveOGM('user:abc123')
resp = client.Request('GET', 'https://gerp.gvsds.com/api/x', scope=['ogm:read'])
```

## 5. Socket.IO 事件协议（PascalCase）

> **重要**：所有事件的请求/响应字段全部使用 **PascalCase**。

| 事件 | 请求字段 | 响应字段 |
|---|---|---|
| `msync:register` | `ServiceName`, `Domain`, `Secret`, `Scope`, `ServiceType`, `Metadata` | `ServiceNode` 序列化 |
| `msync:auth` | `ServiceName`, `Payload`, `Signature` | `AccessToken`, `RefreshToken`, `Scope`, `ExpiresAt` |
| `msync:auth:refresh` | `RefreshToken` | 同上 |
| `msync:heartbeat` | — | — |
| `msync:deregister` | `ServiceName` | — |
| `msync:discover` | `ActiveOnly` | `services[]` |
| `msync:discover:one` | `ServiceName` | `service` |
| `msync:ogm:register` | `ObjectType`, `LocalID`, `ServiceName`, `GlobalID`, `Metadata` | `ogm` |
| `msync:ogm:batch` | `Items[]`, `ServiceName` | `ogms[]` |
| `msync:ogm:resolve` | `GlobalID` | `ogm` |
| `msync:ogm:query` | `ObjectType`/`ServiceName`/`LocalID` | `results` |
| `msync:ogm:update` | `GlobalID`, `Metadata` | `ogm` |
| `msync:ogm:deregister` | `GlobalID` | — |
| `msync:tx:create` | `Name`, `Steps[]`, `Description`, `TransactionType`, `InitiatorServiceName`, `ReferenceOGMUUID`, `Payload`, `TimeoutSeconds` | `dtx` |
| `msync:tx:execute` | `TransactionCode`, `AccessToken` | `dtx` |
| `msync:tx:compensate` | 同上 | `dtx` |
| `msync:tx:cancel` | `TransactionCode` | — |
| `msync:tx:detail` | `TransactionCode` | `dtx` |
| `msync:tx:retry` | `TransactionCode`, `StepIndex`, `AccessToken` | `step` |
| `msync:tx:timeout_check` | — | `TimedOut[]` |
| `msync:tx:query` | `Status` | `dtxs[]` |

服务端推送：

- `msync:service:online` — `{Service, Domain}`
- `msync:service:offline` — `{Service}`

## 6. 快速上手

### 6.1 子服务器（ATestCServer）使用 SDK

[ATestCServer/msync_client/sio_client.py](file:///d:/SMANAGE/ATestCServer/msync_client/sio_client.py) 提供了 `MSyncSocketClient`，**已全部 PascalCase**：

```python
from msync_client.sio_client import MSyncSocketClient

msync = MSyncSocketClient(
    master_url='https://www.gvsds.com',
    service_name='atest-cserver',
    service_domain='http://127.0.0.1:25080',
    service_secret='shared-secret',
    scope=['ogm:read', 'ogm:write', 'registry:read'],
)
msync.Connect()

# OGM
ogm = msync.OGMRegister('user', '123', 'atest-cserver')
resolved = msync.OGMResolve(ogm['GlobalID'])

# 服务发现
for svc in msync.DiscoverAll():
    print(svc['ServiceName'], '->', svc['Domain'])

# 状态
print('已认证:', msync.IsAuthenticated)
print('AccessToken:', msync.AccessToken[:20], '...')

msync.Disconnect()
```

### 6.2 直接使用 SDK 类

```python
from gvsdsdk import ServiceRegistry, OGMManager, TransactionManager

# 注册中心
ServiceRegistry.Register(
    Name='my-service',
    Domain='http://localhost:8080',
    Secret='secret',
)

# OGM
entry = OGMManager.Register('user', '1', 'my-service')

# 事务
dtx = TransactionManager.Create(
    Name='test-tx',
    StepsData=[{'ServiceName': 'svc-a', 'StepName': 'step1'}],
)
```

## 7. 架构示意

```
┌──────────────────┐         ┌──────────────────┐
│  ATestCServer    │         │  gvsds (master)  │
│                  │         │                  │
│ MSyncSocketClient│  ←──┐   │  Socket.IO       │
│   (PascalCase)   │     │   │  Server          │
│                  │     │   │  (PascalCase)    │
│  ┌────────────┐  │     │   │                  │
│  │ gvsdsdk    │  │     │   │  ┌────────────┐  │
│  │  .auth     │◄─┼─────┼───┼──│ msync      │  │
│  │  .ogm      │  │     │   │  │  .auth     │  │
│  │  .registry │  │     │   │  │  .ogm      │  │
│  │  .tx       │  │     │   │  │  .registry │  │
│  │  .client   │  │     │   │  │  .tx       │  │
│  └─────┬──────┘  │     │   │  └─────┬──────┘  │
│        │         │     │   │        │         │
│   薄封装        │     │   │   原生层          │
│        ▼         │     │   │        ▼         │
│  ┌────────────┐  │     │   │  ┌────────────┐  │
│  │ gvsds      │  │     │   │  │ gvsds      │  │
│  │ .apps      │◄─┼─────┘   │  │ .apps      │  │
│  │  .msync    │  │         │  │  .msync    │  │
│  └────────────┘  │         │  └────────────┘  │
└──────────────────┘         └──────────────────┘
```

**转发模块**的类在 `gvsdsdk` 与 `gvsds.apps.msync` 中是**完全同一个对象**——例如 `from gvsdsdk import ServiceRegistry` 与 `from gvsds.apps.msync.registry import ServiceRegistry` 拿到的是同一个类。**自有实现**模块（`msync.auth` / `msync.client` / `msync.local_tx`）的类不依赖主服务器，在子服务器中是独立代码。

## 8. 与项目原生层的集成

### 8.1 ATestCServer

[ATestCServer/manage.py:5](file:///d:/SMANAGE/ATestCServer/manage.py#L5) 在启动时设置 `sys.path`：

```python
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# → D:\SMANAGE  被加入 sys.path
# → import gvsdsdk 即可工作
```

子服务器**不直接 import** `gvsds.apps.msync`（避免 Django settings 依赖），仅通过 SDK 引用。

### 8.2 gvsds 主服务器

[gvsds/settings.py](file:///d:/SMANAGE/gvsds/settings.py) 启动时同样将 `gvsdsdk` 加入 `sys.path`：

```python
# 在 wsgi.py / asgi.py 中
sys.path.append(BASE_DIR)
```

`gvsds.apps.msync` 内部模块（如 [sio.py](file:///d:/SMANAGE/gvsds/apps/msync/sio.py)）会**直接 import** 原生层 `gvsds.apps.msync.auth/registry/ogm/transaction`，**协议 100% 兼容**。

## 9. 测试与验证

```python
import django; django.setup()
import gvsdsdk

# 验证 SDK 类与原生层是同一对象
from gvsdsdk import ServiceRegistry, OGMManager, TransactionManager
from gvsds.apps.msync.registry import ServiceRegistry as NativeSR
from gvsds.apps.msync.ogm import OGMManager as NativeOGM
from gvsds.apps.msync.transaction import TransactionManager as NativeTM

assert ServiceRegistry is NativeSR
assert OGMManager is NativeOGM
assert TransactionManager is NativeTM
print('SDK 薄封装校验通过')
```

## 10. 版本与兼容性

| 项目 | 版本 |
|---|---|
| **gvsdsdk** | 1.0.0 |
| **协议版本** | v1.0 (PascalCase 严格模式) |
| **Python** | >= 3.11 |
| **兼容 GVSDS** | >= 1.0 |
| **兼容 ATestCServer** | >= 1.0 |

## 11. 错误码速查

| 错误码 | 含义 | 建议处理 |
|---|---|---|
| 400 | 参数错误 | 检查请求体字段名（PascalCase） |
| 401 | 认证失败 | 检查 `ServiceSecret` |
| 404 | 资源不存在 | 重新注册服务 |
| 409 | 冲突 | OGM 已注册、服务重名等 |
| 500 | 服务器内部错误 | 查看服务器日志 |

## 12. 维护

- **主服务器**：`gvsds/apps/msync/`（业务实现）
- **SDK 仓库**：`gvsdsdk/`（薄封装）
- **子服务器示例**：`ATestCServer/msync_client/`

修改原生层时，**转发类/函数无需改动 SDK**——它们通过 `from … import …` 直接引用 `gvsds.apps.msync` 原生对象，自动反映最新行为；**自有实现**模块（`msync.auth` 自有部分 / `msync.client` / `msync.local_tx`）为独立代码，需同步维护。

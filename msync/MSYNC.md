# MSync — 微服务通信库

> GVsds 平台微服务架构的多端通信、服务间验权、对象全局映射（OGM）与分布式事务管理。
> 本服务器是**主服务器**（账本服务器），存储所有分布式服务地址，服务间需验权来互相获取 OGM 等信息。
> 实现位于 `gvsdsdk/msync`（SDK 原生实现，非封装层）。

---

## 概述

MSync 是为 GVsds 多域微服务平台设计的通信中间件，运行于**主服务器**（账本服务器），提供以下核心能力：

- **服务注册与发现** — 微服务节点的注册、注销、心跳、密钥轮换
- **服务间 HMAC 签名认证** — 基于 HMAC-SHA256 的双向验权，自包含令牌签发
- **OGM 对象全局映射** — 跨服务对象身份注册与解析
- **分布式事务管理** — 基于 Saga 模式的跨服务事务编排、补偿与重试

### 什么是"账本服务器"？

账本服务器即**主服务器**，它维护一张"账本"——记录所有分布式微服务的地址、状态、密钥等信息。任何微服务要加入集群，必须先向账本服务器注册；任何微服务要与其他服务通信，必须先通过账本服务器验权获取令牌。

---

## 架构

```
                    ┌──────────────────────────────────┐
                    │      账本服务器 (Master)          │
                    │                                  │
                    │  ┌──────────┐  ┌──────────────┐  │
                    │  │ Registry │  │   Auth (HMAC) │  │
                    │  │ (服务账本)│  │  (服务间验权) │  │
                    │  └──────────┘  └──────────────┘  │
                    │  ┌──────────┐  ┌──────────────┐  │
                    │  │   OGM    │  │ Transaction  │  │
                    │  │(对象映射)│  │  (Saga编排)  │  │
                    │  └──────────┘  └──────────────┘  │
                    └──────────┬───────────────────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
        ┌─────┴─────┐   ┌────┴─────┐   ┌─────┴─────┐
        │   GERP    │   │   CRB    │   │    T4     │
        │ (企业ERP) │   │(货币经济)│   │(远程桌面) │
        └───────────┘   └──────────┘   └───────────┘
```

---

## 认证流程

```
  工作服务                         账本服务器                      目标服务
  (Worker)                        (Master)                       (Target)

  1. 构建 AuthPayload ──────▶
     {service, challenge, ts}

  2. HMAC-SHA256 签名 ──────▶
     signature = HMAC(payload, secret)

                                  3. 查找 ServiceNode
                                  4. 验证签名
                                  5. 签发 AccessToken + RefreshToken
                             ◀──── 返回令牌对

  6. 携带令牌请求 ─────────────────────────────────────────▶
     Authorization: Service <AccessToken>

                                                            7. 本地验证令牌
                                                            8. 检查权限范围
                                                            9. 执行业务逻辑
                       ◀───────────────────────────────── 返回结果
```

### 令牌机制

| 令牌类型 | 有效期 | 用途 |
|---------|--------|------|
| AccessToken | 2 小时 | 服务间请求认证 |
| RefreshToken | 30 天 | 刷新 AccessToken |

令牌为**自包含格式**（类似 JWT），目标服务可使用主密钥本地验证，无需回调账本服务器。

### 权限范围（Scope）

| Scope | 说明 |
|-------|------|
| `ogm:read` | 读取 OGM 映射 |
| `ogm:write` | 注册/更新 OGM 映射 |
| `registry:read` | 发现服务 |
| `registry:write` | 注册/注销服务 |
| `tx:read` | 查询事务状态 |
| `tx:write` | 创建/执行/补偿事务 |
| `*` | 全部权限 |

### 密钥认证强制

- 新服务注册必须携带 `ServiceSecret`（与 `ServiceSecretHash` 自洽），否则拒绝
- 除 register / auth / auth:refresh 外，所有 REST 与 SIO 业务操作必须持密钥签发的令牌
- 心跳/注销仅允许操作本服务

---

## 分布式事务管理

### 远程 + 本地双层架构

MSync 采用**远程事务管理器 + 本地事务管理器**双层架构：

```
  主服务器 (远程事务管理器 TransactionManager)
    │
    │  创建事务，编排步骤，管理状态
    │
    ├──▶ POST {gerp_domain}/api/msync/local/execute/
    │    {step_name: "create_user", payload: {...}}
    │    → GERP 的 LocalTransactionManager.execute_step()
    │
    ├──▶ POST {crb_domain}/api/msync/local/execute/
    │    {step_name: "create_wallet", payload: {...}}
    │    → CRB 的 LocalTransactionManager.execute_step()
    │
    └──▶ POST {crb_domain}/api/msync/local/compensate/
         {step_name: "create_wallet", payload: {...}, action_result: {...}}
         → CRB 的 LocalTransactionManager.compensate_step()
```

- **远程事务管理器**（主服务器）：创建事务、编排步骤、管理状态、调用各服务的本地端点
- **本地事务管理器**（每个工作服务）：注册步骤处理器、执行本地逻辑、补偿本地操作

### Saga 模式

```
  Step 0 (GERP)      Step 1 (CRB)       Step 2 (T4)
  创建用户 ────────▶ 创建钱包 ────────▶ 注册终端
     │                  │                   │
     │  补偿:删除用户   │  补偿:删除钱包    │  补偿:注销终端
     ◀──────────────── ◀────────────────── ◀
```

- **正向执行**：远程管理器按步骤顺序调用各服务的本地 execute 端点
- **补偿回滚**：任一步骤失败时，远程管理器逆序调用已完成步骤的本地 compensate 端点
- **超时检测**：自动标记超时事务并触发补偿
- **重试机制**：正向步骤失败可 `RetryStep`，补偿失败可 `RetryCompensate`（可配置最大次数）

### 事务生命周期

```
Pending ──▶ Running ──▶ Committed (全部步骤成功)
                │
                ├─▶ Failed ──▶ Compensating ──▶ Compensated (补偿成功)
                │
                └─▶ TimedOut ──▶ Compensating ──▶ Compensated
```

| 状态 | 代码 | 说明 |
|------|------|------|
| 待执行 | 0 | 事务已创建，尚未开始 |
| 执行中 | 1 | 正在执行步骤 |
| 已提交 | 2 | 全部步骤成功完成 |
| 补偿中 | 3 | 正在逆序执行补偿操作 |
| 已补偿 | 4 | 补偿操作全部完成 |
| 失败 | 5 | 步骤执行失败且补偿未完成 |
| 超时 | 6 | 事务超时 |

### 步骤状态

| 状态 | 代码 | 说明 |
|------|------|------|
| 待执行 | 0 | 步骤尚未开始 |
| 执行中 | 1 | 正在调用 action 端点 |
| 已完成 | 2 | action 调用成功 |
| 补偿中 | 3 | 正在调用 compensate 端点 |
| 已补偿 | 4 | compensate 调用成功 |
| 失败 | 5 | 调用失败 |
| 已跳过 | 6 | 事务取消时跳过 |

---

## 数据模型

### ServiceNode — 服务节点（账本记录）

| 字段 | 类型 | 说明 |
|------|------|------|
| ServiceUUID | BINARY(16) PK | 服务唯一标识 |
| ServiceName | VARCHAR(128) UNIQUE | 服务名称 |
| ServiceDomain | VARCHAR(256) | 服务域名/URL |
| ServiceSecretHash | VARCHAR(128) | bcrypt 哈希密钥 |
| ServiceScope | JSON | 权限范围列表 |
| ServiceStatus | SMALLINT | 状态 (1=活跃) |
| ServiceType | SMALLINT | 类型 (0=工作节点, 1=主节点) |
| ServiceMetadata | JSON | 服务元数据 |
| CreateTime | DATETIME | 注册时间 |
| LastHeartbeat | DATETIME | 最后心跳时间 |

### ServiceToken — 服务令牌

| 字段 | 类型 | 说明 |
|------|------|------|
| TokenUUID | BINARY(16) PK | 令牌唯一标识 |
| ServiceUUID | FK → ServiceNode | 所属服务 |
| AccessToken | VARCHAR(512) UNIQUE | 访问令牌 |
| RefreshToken | VARCHAR(512) UNIQUE | 刷新令牌 |
| TokenScope | JSON | 令牌权限范围 |
| ExpiresAt | DATETIME | 访问令牌过期时间 |
| RefreshExpiresAt | DATETIME | 刷新令牌过期时间 |
| TokenStatus | SMALLINT | 状态 (1=有效, 0=已撤销) |

### ObjectGlobalMapping — OGM 对象全局映射

| 字段 | 类型 | 说明 |
|------|------|------|
| OGMUUID | BINARY(16) PK | 映射唯一标识 |
| ObjectType | VARCHAR(64) | 对象类型 (user, company, asset...) |
| ObjectLocalID | VARCHAR(128) | 本地 ID |
| ObjectGlobalID | VARCHAR(128) UNIQUE | 全局 ID |
| OwnerServiceUUID | FK → ServiceNode | 所属服务 |
| ObjectMetadata | JSON | 对象元数据 |
| ObjectStatus | SMALLINT | 状态 (1=活跃, 0=已注销) |

**唯一约束**: `(ObjectType, ObjectLocalID, OwnerServiceUUID)`

### DistributedTransaction — 分布式事务

| 字段 | 类型 | 说明 |
|------|------|------|
| TransactionUUID | BINARY(16) PK | 事务唯一标识 |
| TransactionCode | VARCHAR(64) UNIQUE | 事务代码 (DTX-XXXXXXXXXXXX) |
| TransactionType | VARCHAR(16) | 事务类型 (saga/2pc) |
| TransactionName | VARCHAR(128) | 事务名称 |
| TransactionDescription | VARCHAR(512) | 描述 |
| TransactionStatus | SMALLINT | 事务状态 |
| InitiatorServiceUUID | FK → ServiceNode | 发起服务 |
| ReferenceOGMUUID | BINARY(16) | 关联 OGM |
| TransactionPayload | JSON | 事务级载荷 |
| TransactionResult | JSON | 事务结果 |
| TimeoutSeconds | INT | 超时秒数 (默认 300) |
| CreateTime | DATETIME | 创建时间 |
| EndTime | DATETIME | 结束时间 |

### TransactionStep — 事务步骤

| 字段 | 类型 | 说明 |
|------|------|------|
| StepUUID | BINARY(16) PK | 步骤唯一标识 |
| TransactionUUID | FK → DistributedTransaction | 所属事务 |
| StepIndex | INT | 步骤序号 |
| StepName | VARCHAR(128) | 步骤名称 |
| ParticipantServiceUUID | FK → ServiceNode | 参与服务 |
| StepStatus | SMALLINT | 步骤状态 |
| ActionEndpoint | VARCHAR(512) | 正向操作端点 |
| ActionMethod | VARCHAR(8) | 正向操作方法 |
| ActionPayload | JSON | 正向操作载荷 |
| CompensateEndpoint | VARCHAR(512) | 补偿操作端点 |
| CompensateMethod | VARCHAR(8) | 补偿操作方法 |
| CompensatePayload | JSON | 补偿操作载荷 |
| StepResult | JSON | 步骤执行结果 |
| RetryCount | INT | 已重试次数 |
| MaxRetries | INT | 最大重试次数 (默认 3) |

---

## API 端点

### 认证

| 方法 | 端点 | 说明 |
|------|------|------|
| POST | `/api/msync/auth/token/` | 服务认证获取令牌 |
| POST | `/api/msync/auth/token/refresh/` | 刷新令牌 |

### 服务注册与发现（账本）

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/api/msync/registry/` | 列出所有已注册服务 |
| POST | `/api/msync/registry/` | 注册新服务（必须携带密钥） |
| GET | `/api/msync/registry/<name>/` | 发现指定服务 |
| POST | `/api/msync/registry/<name>/heartbeat/` | 服务心跳（需令牌，仅本服务） |
| DELETE | `/api/msync/registry/<name>/deregister/` | 注销服务（需令牌，仅本服务） |

### OGM 对象全局映射

| 方法 | 端点 | 说明 |
|------|------|------|
| POST | `/api/msync/ogm/` | 注册对象映射 |
| POST | `/api/msync/ogm/batch/` | 批量注册 |
| GET | `/api/msync/ogm/<global_id>/` | 解析全局 ID |
| GET | `/api/msync/ogm/query/` | 按类型/服务查询 |
| PUT | `/api/msync/ogm/<global_id>/update/` | 更新元数据 |
| DELETE | `/api/msync/ogm/<global_id>/deregister/` | 注销映射 |

### 分布式事务

| 方法 | 端点 | 说明 |
|------|------|------|
| POST | `/api/msync/tx/` | 创建事务 |
| GET | `/api/msync/tx/query/?status=0` | 按状态查询事务 |
| POST | `/api/msync/tx/timeout-check/` | 检测超时事务 |
| GET | `/api/msync/tx/<code>/` | 事务详情 |
| POST | `/api/msync/tx/<code>/execute/` | 执行事务 |
| POST | `/api/msync/tx/<code>/compensate/` | 补偿事务 |
| POST | `/api/msync/tx/<code>/cancel/` | 取消事务 |
| POST | `/api/msync/tx/<code>/retry/` | 重试正向失败步骤 |
| POST | `/api/msync/tx/<code>/retry-compensate/` | 重试补偿失败步骤 |

### 本地事务管理器（每个工作服务）

| 方法 | 端点 | 说明 |
|------|------|------|
| POST | `/api/msync/local/execute/` | 执行本地步骤（由主服务器调用） |
| POST | `/api/msync/local/compensate/` | 补偿本地步骤（由主服务器调用） |
| GET | `/api/msync/local/handlers/` | 列出已注册的步骤处理器 |

**创建事务示例**:
```json
POST /api/msync/tx/
{
    "name": "用户注册流程",
    "description": "跨服务用户注册：创建用户→创建钱包→注册终端",
    "transaction_type": "saga",
    "timeout_seconds": 300,
    "steps": [
        {
            "name": "创建用户",
            "participant_service": "gerp",
            "action_endpoint": "https://gerp.gvsds.com/api/users/",
            "action_payload": {"username": "newuser"},
            "compensate_endpoint": "https://gerp.gvsds.com/api/users/compensate/",
            "compensate_payload": {"username": "newuser"}
        },
        {
            "name": "创建钱包",
            "participant_service": "crb",
            "action_endpoint": "https://crb.gvsds.com/api/wallets/",
            "action_payload": {"owner": "newuser"},
            "compensate_endpoint": "https://crb.gvsds.com/api/wallets/compensate/",
            "compensate_payload": {"owner": "newuser"}
        },
        {
            "name": "注册终端",
            "participant_service": "t4",
            "action_endpoint": "https://t4.gvsds.com/api/terminals/",
            "action_payload": {"user": "newuser"},
            "compensate_endpoint": "https://t4.gvsds.com/api/terminals/compensate/",
            "compensate_payload": {"user": "newuser"}
        }
    ]
}
```

**执行事务**:
```json
POST /api/msync/tx/DTX-ABC123DEF456/execute/
{
    "access_token": "eyJhbGci..."
}
```

**重试失败步骤**:
```json
POST /api/msync/tx/DTX-ABC123DEF456/retry/
{
    "step_index": 1,
    "access_token": "eyJhbGci..."
}
```

**重试补偿失败步骤**:
```json
POST /api/msync/tx/DTX-ABC123DEF456/retry-compensate/
{
    "step_index": 1,
    "access_token": "eyJhbGci..."
}
```

---

## Python API

### 服务注册（账本操作）

```python
from gvsdsdk.msync.registry import ServiceRegistry, RegistryError

node = ServiceRegistry.Register(
    name='gerp',
    domain='https://gerp.gvsds.com',
    secret='my-super-secret-key',
    scope=['ogm:read', 'ogm:write', 'tx:read', 'tx:write'],
)
info = ServiceRegistry.Discover('gerp')
ServiceRegistry.Heartbeat('gerp')
ServiceRegistry.RotateSecret('gerp', 'new-secret-key')
ServiceRegistry.Deregister('gerp')
```

### 服务客户端

```python
from gvsdsdk.msync.client import ServiceClient

client = ServiceClient('gerp', 'secret', 'https://api.gvsds.com')
client.Authenticate()
ogm = client.ResolveOGM('user:550e8400')
info = client.DiscoverService('crb')
```

### OGM 管理

```python
from gvsdsdk.msync.ogm import OGMManager

ogm = OGMManager.Register('user', 'uuid-1', 'core', global_id='user:uuid1')
info = OGMManager.Resolve('user:uuid1')
users = OGMManager.QueryByType('user')
OGMManager.Deregister('user:uuid1')
```

### 分布式事务

```python
from gvsdsdk.msync.transaction import TransactionManager, TransactionError

# 创建事务
dtx = TransactionManager.Create(
    name='用户注册流程',
    steps_data=[
        {
            'name': 'create_user',
            'participant_service': 'gerp',
            'action_payload': {'username': 'newuser'},
            'compensate_payload': {'username': 'newuser'},
        },
        {
            'name': 'create_wallet',
            'participant_service': 'crb',
            'action_payload': {'owner': 'newuser'},
            'compensate_payload': {'owner': 'newuser'},
        },
    ],
    timeout_seconds=300,
)

# 执行事务（远程管理器自动调用各服务的本地管理器）
dtx = TransactionManager.Execute(dtx.TransactionCode, access_token='...')

# 查看详情
detail = TransactionManager.GetDetail(dtx.TransactionCode)

# 手动补偿
dtx = TransactionManager.Compensate(dtx.TransactionCode, access_token='...')

# 重试正向失败步骤
step = TransactionManager.RetryStep(dtx.TransactionCode, step_index=1, access_token='...')

# 重试补偿失败步骤
step = TransactionManager.RetryCompensate(dtx.TransactionCode, step_index=1, access_token='...')

# 取消待执行事务
TransactionManager.Cancel(dtx.TransactionCode)

# 检测超时
timed_out = TransactionManager.CheckTimeouts()

# 按状态查询
running = TransactionManager.QueryByStatus(1)
```

### 本地事务管理器（工作服务端）

```python
from gvsdsdk.msync.local_tx import local_tx

# 注册步骤处理器（在服务启动时）
def create_user_action(payload):
    user = User.objects.create(username=payload['username'])
    return {'user_uuid': str(user.UserUUID)}

def create_user_compensate(payload, action_result):
    User.objects.filter(username=payload['username']).delete()
    return {'deleted': True}

local_tx.register(
    'create_user',
    action=create_user_action,
    compensate=create_user_compensate,
)

# 列出已注册处理器
handlers = local_tx.list_handlers()

# 注销处理器
local_tx.unregister('create_user')
```

### 权限装饰器

```python
from gvsdsdk.msync.middleware import require_service_scope, ServicePermission

@require_service_scope('ogm:read')
def my_view(request):
    pass

class MyViewSet(ViewSet):
    permission_classes = [ServicePermission('tx:read', 'tx:write')]
```

---

## 配置

### settings.py

```python
INSTALLED_APPS = [
    'gvsdsdk.msync',
]

MIDDLEWARE = [
    'gvsdsdk.msync.middleware.ServiceAuthMiddleware',
]
```

### 数据库路由

msync 使用 `default` 数据库（Users 库），与 core、gerp 共享。

### secrets.ini

```ini
[msync]
MASTER_SECRET = your-master-secret-for-token-signing
```

---

## 文件清单

| 文件 | 说明 |
|------|------|
| `models.py` | 5 个数据模型（ServiceNode, ServiceToken, ObjectGlobalMapping, DistributedTransaction, TransactionStep） |
| `auth.py` | HMAC 签名 + 自包含令牌编解码 + 服务端签发/验证 |
| `client.py` | ServiceClient 跨服务请求客户端 |
| `middleware.py` | 服务认证中间件 + 权限装饰器 |
| `registry.py` | 服务注册/发现/心跳/密钥轮换 |
| `ogm.py` | OGM 对象全局映射管理 |
| `transaction.py` | 远程事务管理器（Saga 编排、补偿、重试、超时，调用本地管理器） |
| `local_tx.py` | 本地事务管理器（步骤处理器注册、本地执行与补偿） |
| `sio.py` | Socket.IO 服务端（注册/认证/心跳/OGM/事务事件 + 密钥认证守卫） |
| `views.py` | REST API 视图 |
| `serializers.py` | DRF 序列化器 |
| `urls.py` | API 路由 |
| `admin.py` | Django Admin 注册 |

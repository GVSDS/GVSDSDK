# GVSDSDK 版本与连接状态

> 主服务器（gvsds）与子服务器（如 ATestCServer）通过 MSYNC 协议连接，
> 双端必须安装 gvsdsdk。本表说明各版本组合的连接状态与升级建议。

## 版本规则

- **版本来源单一**：`gvsdsdk/__init__.py` 的 `VERSION_MAJOR/MINOR/PATCH`（pyproject.toml 同步）。
- **启动校验**：主服务器 `gvsds/settings.py` 与子服务器 `apps/sub_server/apps.py`
  均调用 `gvsdsdk.check_min_version(REQUIRED)`，不满足时**告警不阻断**。
- **1.1.0 重要变更**：msync 全部实现从 `gvsds.apps.msync` 迁入 `gvsdsdk/msync`
  （SDK 原生实现，可脱离 gvsds 独立部署）；Saga 增加自动重试/步骤数据流/空闲超时/
  TransactionResult 汇总/幂等键；注册强制密钥认证。

## 连接状态表

| 主服务器 SDK | 子服务器 SDK | 连接状态 | 说明 |
|---|---|---|---|
| 1.0.0 | 任意 | ⛔ 不可用 | 1.0.0 主侧 msync 位于 `gvsds.apps.msync`，该路径已在 1.1.0 中删除，无法再部署 |
| 1.1.0 | 1.1.0 | ✅ 兼容（推荐） | 版本校验通过，协议一致，全部能力可用 |
| 1.1.0 | 1.0.0 | ⚠️ 兼容（建议升级） | 子端使用的 `gvsdsdk.msync.auth/client/local_tx` 接口 1.0.0→1.1.0 未变，可正常连接；子端启动会因 `check_min_version('1.1.0')` 不满足打告警 |
| 1.0.0（从） | 1.1.0（主） | ⛔ 版本不满足 | 主服务器 `check_min_version('1.1.0')` 失败，功能不受保障 |

> 说明：`check_min_version` 为“当前 SDK ≥ 要求版本”语义。新部署请双端统一升级到 1.1.0。

## 版本历史

| 版本 | 关键变化 |
|---|---|
| 1.0.0 | msync 为薄封装（转发 `gvsds.apps.msync`）；Saga 基础编排/补偿 |
| 1.1.0 | msync 整体搬迁至 `gvsdsdk/msync`（原生实现，脱离 gvsds）；密钥认证强制；Saga 自动重试 / 步骤数据流 / 空闲超时 / TransactionResult / 幂等键；SDK 全模块独立导入验证 |

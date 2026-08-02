# 双端依赖安装指南

> 本节描述如何让 `gvsds` 主服务器和 `ATestCServer` 等子服务器**共享同一版本**的 `gvsdsdk`。

## 1. 版本一致性原则

- **同一代码**：双端导入的是**同一份 Python 文件**（物理位置 `D:\SMANAGE\gvsdsdk`）
- **同一版本**：通过 `pyproject.toml` 中的 `version = "1.0.0"` 锁定
- **启动校验**：双端启动时调用 `gvsdsdk.check_min_version("1.0.0")`，版本不符会打 WARNING 日志
- **热重载支持**：直接编辑 `D:\SMANAGE\gvsdsdk` 下的文件即可生效（无需 reinstall）

## 2. 三种安装方式

### 方式 A：PYTHONPATH 方式（推荐开发用）

```bash
# 启动 gvsds
cd D:\SMANAGE\gvsds
set PYTHONPATH=D:\SMANAGE\gvsds;D:\SMANAGE\gvsdsdk
python manage.py runserver

# 启动 ATestCServer
cd D:\SMANAGE\ATestCServer
set PYTHONPATH=D:\SMANAGE\ATestCServer;D:\SMANAGE\gvsdsdk
python manage.py runserver 0.0.0.0:25080
```

`manage.py` 和 `settings.py` 内已自动 `sys.path.append(...)`，无需手动设置。

### 方式 B：可编辑 pip 安装（推荐开发用，自动同步）

```bash
# 全局安装一次即可，所有项目共享
pip install -e D:\SMANAGE\gvsdsdk

# 验证
pip show gvsdsdk
# Name: gvsdsdk
# Version: 1.0.0
# Location: D:\SMANAGE\gvsdsdk
```

### 方式 C：路径式 requirements 固定（推荐生产/部署）

`gvsds/requirements.txt`：
```
gvsdsdk==1.0.0
```

`ATestCServer/requirements.txt`：
```
gvsdsdk==1.0.0
```

部署时 `pip install -r requirements.txt` 会自动从 PyPI 拉取对应版本（或私有源）。

## 3. 启动时的版本校验日志

**正常启动**（两端版本一致）：

```
gvsds 端:
[INFO] gvsds.startup: [SDK] 加载: v1.0.0 @ D:\SMANAGE\gvsdsdk
[INFO] gvsds.startup: [SDK] OK: SDK 1.0.0 >= 1.0.0

ATestCServer 端:
[INFO] apps: [SDK] 加载: v1.0.0 @ D:\SMANAGE\gvsdsdk
[INFO] apps: [SDK] OK: SDK 1.0.0 >= 1.0.0
```

**版本不一致**（双端版本不同）：

```
[WARN] gvsds.startup: [SDK] 版本不符！FAIL: SDK 0.9.0 < 1.0.0 (required)。双端 gvsdsdk 版本必须一致！
```

## 4. CLI 验证工具

```bash
# 默认：显示简要信息
python -m gvsdsdk

# 仅版本号（适合脚本中调用）
python -m gvsdsdk --version
# 1.0.0

# 详细信息
python -m gvsdsdk --info
# GVSDS SDK Version Information:
#   Version      : 1.0.0
#   Major        : 1
#   Minor        : 0
#   Patch        : 0
#   Prerelease   :
#   Location     : D:\SMANAGE\gvsdsdk
#   Python       : 3.12.0
#   Platform     : Windows-10-...
#   Machine      : AMD64

# 最低版本检查（CI 友好）
python -m gvsdsdk --check 1.0.0
# OK: SDK 1.0.0 >= 1.0.0  (返回 0)

python -m gvsdsdk --check 2.0.0
# FAIL: SDK 1.0.0 < 2.0.0 (required)  (返回 1)

# 列出所有公开符号
python -m gvsdsdk --symbols

# 测试连通性
python -m gvsdsdk --ping
# Pong — SDK 1.0.0 ready at D:\SMANAGE\gvsdsdk
#   23 symbols available (lazy)
```

## 5. 升级 SDK 版本

当需要发布新版本时：

1. **修改 `D:\SMANAGE\gvsdsdk\__init__.py`**：
   ```python
   VERSION_MAJOR = 1
   VERSION_MINOR = 1  # 升级
   VERSION_PATCH = 0
   ```

2. **修改 `D:\SMANAGE\gvsdsdk\pyproject.toml`**：
   ```toml
   [project]
   version = "1.1.0"
   ```

3. **同步更新双端 `requirements.txt`**：
   ```
   gvsdsdk==1.1.0
   ```

4. **同步更新双端最低版本常量**：
   - `gvsds/settings.py:17` → `_REQUIRED_GVSDSDK = '1.1.0'`
   - `ATestCServer/apps/sub_server/apps.py:12` → `REQUIRED_GVSDSDK_VERSION = '1.1.0'`

5. **重启双端** → 启动日志会报告新版本

## 6. 常见问题

| 现象 | 原因 | 解决 |
|---|---|---|
| `ImportError: No module named 'gvsdsdk'` | PYTHONPATH 未设置 | 安装 SDK 或加入路径 |
| `WARNING [SDK] 版本不符` | 双端版本不一致 | 升级较低端 |
| `WARNING [SDK] gvsdsdk 未安装` | 子服务器 PYTHONPATH 漏配 | 检查 `manage.py:5` |
| 编辑 SDK 后无变化 | 字节码缓存 | 删除 `__pycache__/` |

## 7. 跨平台路径

`pyproject.toml` 路径引用应使用 `file:///` 协议：

```toml
gvsdsdk @ file:///D:/SMANAGE/gvsdsdk ; sys_platform == "win32"
gvsdsdk @ file:///opt/gvsdsdk          ; sys_platform == "linux"
gvsdsdk @ file:///usr/local/gvsdsdk    ; sys_platform == "darwin"
```

或在 CI 中使用环境变量：

```bash
pip install -e "$GVS_SDK_PATH"
```

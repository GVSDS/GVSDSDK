"""模块：SDK 通用配置读取（可注入提供者）

SDK 内不得反向依赖业务层（gvsds.app_config），故提供配置读取钩子：
    - 业务侧启动时调用 SetConfigProvider(gvsds.app_config.get, gvsds.app_config.getlist) 注入；
    - 未注入时回退到环境变量 ``{SECTION}_{KEY}``（大写），与 gvsds.secrets 约定一致。

用法（业务侧 shim）：
    from gvsdsdk.config import SetConfigProvider
    from gvsds.app_config import get, getlist
    SetConfigProvider(get, getlist)
"""
import os

_ConfigGet = None
_ConfigGetList = None


def SetConfigProvider(Get, GetList):
    """注入配置读取器（Get(section,key,fallback) / GetList(section,key,fallback,separator)）"""
    global _ConfigGet, _ConfigGetList
    _ConfigGet = Get
    _ConfigGetList = GetList


def Get(section, key, fallback=None):
    """读取单项配置：优先注入的提供者，其次环境变量"""
    if _ConfigGet is not None:
        return _ConfigGet(section, key, fallback=fallback)
    return os.environ.get(f'{section.upper()}_{key.upper()}', fallback)


def GetList(section, key, fallback=None, separator=','):
    """读取列表配置：优先注入的提供者，其次环境变量（分隔符切分）"""
    if _ConfigGetList is not None:
        return _ConfigGetList(section, key, fallback=fallback, separator=separator)
    raw = os.environ.get(f'{section.upper()}_{key.upper()}')
    if raw is None:
        return fallback if fallback is not None else []
    return [item.strip() for item in raw.split(separator) if item.strip()]

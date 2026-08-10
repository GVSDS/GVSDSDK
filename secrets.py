"""GVSDS SDK 配置与密钥访问

环境变量优先，其次读取项目根目录 secrets.ini。
与 gvsds.secrets 同源（同一 secrets.ini），供 SDK 在无 gvsds 依赖时使用。

env 键规则：`{SECTION}_{KEY}` 全大写（如 MSYNC_MASTER_SECRET）。
"""

import os
import configparser
from pathlib import Path

_SECRETS_FILE = Path(__file__).resolve().parent.parent / "secrets.ini"
_config = None


def _load_config():
    global _config
    if _config is not None:
        return _config
    _config = configparser.ConfigParser()
    if _SECRETS_FILE.exists():
        _config.read(str(_SECRETS_FILE), encoding="utf-8")
    return _config


def get(section, key, fallback=None):
    cfg = _load_config()
    env_key = f"{section.upper()}_{key.upper()}"
    env_val = os.environ.get(env_key)
    if env_val is not None:
        return env_val
    return cfg.get(section, key, fallback=fallback)


def getint(section, key, fallback=0):
    val = get(section, key, fallback=None)
    if val is None:
        return fallback
    try:
        return int(val)
    except (ValueError, TypeError):
        return fallback


def getfloat(section, key, fallback=0.0):
    val = get(section, key, fallback=None)
    if val is None:
        return fallback
    try:
        return float(val)
    except (ValueError, TypeError):
        return fallback


def getboolean(section, key, fallback=False):
    val = get(section, key, fallback=None)
    if val is None:
        return fallback
    return val.lower() in ("true", "1", "yes", "on")


def reload():
    global _config
    _config = None
    _load_config()

import re
import uuid
from functools import lru_cache


class UUIDObj:
    __slots__ = ('_raw',)

    def __init__(self, val):
        if val is None:
            self._raw = None
        elif isinstance(val, UUIDObj):
            self._raw = val._raw
        elif isinstance(val, uuid.UUID):
            self._raw = val
        elif isinstance(val, bytes):
            if len(val) != 16:
                raise ValueError(
                    'UUIDObj expects 16-byte bytes, got %d bytes' % len(val)
                )
            self._raw = uuid.UUID(bytes=val)
        elif isinstance(val, str):
            self._raw = uuid.UUID(val)
        else:
            self._raw = uuid.UUID(str(val))

    @property
    def str(self):
        if self._raw is None:
            return None
        return str(self._raw)

    @property
    def bytes(self):
        if self._raw is None:
            return None
        return self._raw.bytes

    @property
    def uuid(self):
        return self._raw

    # ── PascalCase 别名（兼容旧 gvsds.common.UUID 的 Str/Bytes/UUID 属性） ──

    @property
    def Str(self):
        return self.str

    @property
    def Bytes(self):
        return self.bytes

    @property
    def UUID(self):
        return self._raw

    def __str__(self):
        return self.str or ''

    def __repr__(self):
        return f'UUIDObj({self.str})'

    def __eq__(self, other):
        if isinstance(other, UUIDObj):
            return self._raw == other._raw
        if isinstance(other, uuid.UUID):
            return self._raw == other
        if isinstance(other, str):
            return self.str == other
        if isinstance(other, bytes) and len(other) == 16:
            return self.bytes == other
        return NotImplemented

    def __hash__(self):
        return hash(self._raw)

    def __bool__(self):
        return self._raw is not None

    def __len__(self):
        # UUID 固定 16 字节；空值返回 0
        return 16 if self._raw is not None else 0


@lru_cache(maxsize=1)
def _GetUserModel():
    """动态获取 User 模型——保持 SDK 独立性，不硬依赖 gvsds 内部模块。

    回退顺序：
    1. gvsdsdk.User（抽象基类，会触发 LookupError）
    2. 业务子项目中的具体 User 子类（users.User / core.User）

    使用 lru_cache 缓存避免热路径重复调用 apps.get_model()。
    测试中如需重置缓存，调用 _GetUserModel.cache_clear()。
    """
    from django.apps import apps
    for app_label in ('gvsdsdk', 'users', 'core'):
        try:
            return apps.get_model(app_label, 'User')
        except LookupError:
            continue
    raise LookupError("未找到 User 模型，请在业务子项目中定义 User 子类")


# ── UUID 便捷函数（兼容旧 gvsds.common.UUID 的模块级 API） ──

def NewUUID():
    """生成随机 UUIDObj"""
    return UUIDObj(uuid.uuid4())


def NewUUIDBytes():
    """生成随机 UUID 的 16 字节形式"""
    return uuid.uuid4().bytes


def NewUUIDStr():
    """生成随机 UUID 字符串"""
    return str(uuid.uuid4())


def UUIDToBytes(value):
    """任意 UUID 表示 → 16 字节"""
    return UUIDObj(value).bytes


def UUIDToStr(value):
    """任意 UUID 表示 → 字符串"""
    return UUIDObj(value).str


def BytesToUUIDStr(value):
    """16 字节 → UUID 字符串"""
    return str(uuid.UUID(bytes=value))


def StrToUUIDBytes(value):
    """UUID 字符串 → 16 字节"""
    return uuid.UUID(value).bytes

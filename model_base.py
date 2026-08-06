"""QModel 兼容层基类模块

提供 SQLAlchemy 风格的链式 OOP 查询接口，使 Django ORM 支持：

- ``Model.query.filter(...)`` — 链式 FluentQuery 查询
- ``Model.field_name`` — 类级别返回 FieldExpression（支持 ``==``, ``>``, ``in_()`` 等运算符）
- ``Model.field_name`` — 实例级别返回字段值（与原生 Django Model 一致）
- ``Model[condition]`` — 快捷 filter 语法

典型用法::

    # 链式查询
    orders = Dingdan.query.filter(zhuangtai=8).order_by('-create_time').to_list()

    # 字段表达式
    Dingdan.query.filter(Dingdan.jine > 100, Dingdan.zhuangtai.in_([1, 2, 3]))

    # 聚合
    Dingdan.query.aggregate(total=Sum('jine'))

    # 分页
    Dingdan.query.paginate(page=1, per_page=20)
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, Generic, Optional, Self, Tuple, Type, TypeVar, Union

from django.db import models
from django.db.models import Q
from django.core.exceptions import ImproperlyConfigured

from .fluent import FluentQuery


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

#: classproperty 返回值类型变量
_RT = TypeVar('_RT')

#: QModel 子类类型变量（仅用于 __class_getitem__ 兼容）
_QM = TypeVar('_QM', bound='QModel')


class classproperty(Generic[_RT]):
    """类属性描述符，支持 ``Model.query`` 等类级别属性访问。

    泛型参数 ``_RT`` 传播函数返回类型，使 Pylance 能穿透描述符推断
    ``Model.query`` 的返回类型为 ``FluentQuery[Self]``。
    """

    def __init__(self, func: Callable[[type], _RT]) -> None:
        self.fget = func

    def __get__(self, obj: Any, objtype: Optional[type] = None) -> _RT:
        return self.fget(objtype if objtype is not None else type(obj))


ABBREVIATIONS: Dict[str, str] = {
    'uuid': 'UUID', 'id': 'ID', 'ip': 'IP', 'url': 'URL', 'uri': 'URI',
    'api': 'API', 'html': 'HTML', 'css': 'CSS', 'js': 'JS', 'json': 'JSON',
    'xml': 'XML', 'sql': 'SQL', 'http': 'HTTP', 'https': 'HTTPS',
    'smtp': 'SMTP', 'ssl': 'SSL', 'tls': 'TLS', 'ssh': 'SSH', 'ftp': 'FTP',
    '2fa': '2FA', 'elt': 'ELT', 'sve': 'SVE', 't4': 'T4', 'mc': 'MC',
    'crb': 'CRB', 'ccgo': 'CCGO', 'ugm': 'UGM', 'gerp': 'GERP',
    'abac': 'ABAC', 'rbac': 'RBAC', 'smtm': 'SMTM', 'pwd': 'PWD',
}


def snake_to_pascal(name: str) -> str:
    """将 snake_case 转换为 PascalCase，自动识别常见缩写。

    >>> snake_to_pascal('user_uuid')
    'UserUUID'
    >>> snake_to_pascal('paifa_dingdan_shu')
    'PaifaDingdanShu'
    """
    if '_' not in name:
        return name[0].upper() + name[1:] if name else name
    parts = name.split('_')
    result = []
    for p in parts:
        lower = p.lower()
        if lower in ABBREVIATIONS:
            result.append(ABBREVIATIONS[lower])
        else:
            result.append(p.capitalize())
    return ''.join(result)


def pascal_to_snake(name: str) -> str:
    """将 PascalCase 转换为 snake_case。

    >>> pascal_to_snake('UserUUID')
    'user_uuid'
    """
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    s2 = re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1)
    return s2.lower()


# ---------------------------------------------------------------------------
# 字段表达式（类级别访问 Model.field 时返回）
# ---------------------------------------------------------------------------

class _InvertedFieldExpression:
    """取反字段表达式，由 ``~Model.field`` 产生，支持 ``~field.startswith(...)`` 等否定查询。"""

    def __init__(self, field_expr: FieldExpression) -> None:
        self._field_expr = field_expr

    def startswith(self, value: str) -> Q:
        return ~Q(**{f'{self._field_expr._django_name}__startswith': value})

    def contains(self, value: str) -> Q:
        return ~Q(**{f'{self._field_expr._django_name}__contains': value})

    def like(self, pattern: str) -> Q:
        clean = pattern.strip('%')
        return ~Q(**{f'{self._field_expr._django_name}__icontains': clean})

    def in_(self, values) -> Q:
        return ~Q(**{f'{self._field_expr._django_name}__in': values})

    def is_(self, other) -> Q:
        if other is None:
            return ~Q(**{f'{self._field_expr._django_name}__isnull': True})
        return ~Q(**{self._field_expr._django_name: other})

    def between(self, low, high) -> Q:
        return ~(Q(**{f'{self._field_expr._django_name}__gte': low})
                 & Q(**{f'{self._field_expr._django_name}__lte': high}))

    def __repr__(self) -> str:
        return f'~{self._field_expr!r}'


class FieldExpression:
    """字段表达式，由类级别 ``Model.field_name`` 访问产生。

    支持通过 Python 运算符构建 Django Q 对象::

        Model.field == value      → Q(field=value)
        Model.field > value       → Q(field__gt=value)
        Model.field.in_([1,2,3])  → Q(field__in=[1,2,3])
        Model.field.between(a, b) → Q(field__gte=a) & Q(field__lte=b)
        ~Model.field              → _InvertedFieldExpression（否定查询）
    """

    _resolve_cache: Dict[Tuple[str, str], str] = {}

    def __init__(self, model_class: Type, field_name: str) -> None:
        self.model_class = model_class
        self.field_name = field_name
        self._django_name: str = self._resolve_django_name()

    def _resolve_django_name(self) -> str:
        """将 Python 字段名解析为 Django ORM 查询使用的字段名。"""
        cache_key = (self.model_class.__name__, self.field_name)
        if cache_key in FieldExpression._resolve_cache:
            return FieldExpression._resolve_cache[cache_key]
        if hasattr(self.model_class, '_meta'):
            for f in self.model_class._meta.local_fields:
                if f.name == self.field_name:
                    FieldExpression._resolve_cache[cache_key] = f.name
                    return f.name
        FieldExpression._resolve_cache[cache_key] = self.field_name
        return self.field_name

    # ---- 比较运算符 ----

    def __eq__(self, other) -> Q:  # type: ignore[override]
        if isinstance(other, FieldExpression):
            return NotImplemented
        if other is None:
            return Q(**{f'{self._django_name}__isnull': True})
        return Q(**{self._django_name: other})

    def __ne__(self, other) -> Q:  # type: ignore[override]
        if isinstance(other, FieldExpression):
            return NotImplemented
        return ~Q(**{self._django_name: other})

    def __gt__(self, other) -> Q:
        return Q(**{f'{self._django_name}__gt': other})

    def __ge__(self, other) -> Q:
        return Q(**{f'{self._django_name}__gte': other})

    def __lt__(self, other) -> Q:
        return Q(**{f'{self._django_name}__lt': other})

    def __le__(self, other) -> Q:
        return Q(**{f'{self._django_name}__lte': other})

    def __hash__(self) -> int:
        return hash((self.model_class.__name__, self.field_name))

    # ---- 逻辑运算符 ----

    def _ToQ(self) -> Q:
        """将字段表达式转换为 Q 对象（字段非空即真，等价 SQLAlchemy exists()）"""
        return Q(**{f'{self._django_name}__isnull': False})

    def __and__(self, other) -> Q:
        if isinstance(other, Q):
            return self._ToQ() & other
        if isinstance(other, FieldExpression):
            return self._ToQ() & other._ToQ()
        return NotImplemented

    def __or__(self, other) -> Q:
        if isinstance(other, Q):
            return self._ToQ() | other
        if isinstance(other, FieldExpression):
            return self._ToQ() | other._ToQ()
        return NotImplemented

    def __rand__(self, other) -> Q:
        """支持 Q(...) & Model.field 反向运算"""
        if isinstance(other, Q):
            return other & self._ToQ()
        return NotImplemented

    def __ror__(self, other) -> Q:
        """支持 Q(...) | Model.field 反向运算"""
        if isinstance(other, Q):
            return other | self._ToQ()
        return NotImplemented

    def __invert__(self) -> _InvertedFieldExpression:
        return _InvertedFieldExpression(self)

    # ---- 查询方法 ----

    def is_(self, other) -> Q:
        """``field IS NULL`` 或 ``field IS value``。"""
        if other is None:
            return Q(**{f'{self._django_name}__isnull': True})
        return Q(**{self._django_name: other})

    def is_not(self, other) -> Q:
        """``field IS NOT NULL`` 或 ``field IS NOT value``。"""
        if other is None:
            return Q(**{f'{self._django_name}__isnull': False})
        return ~Q(**{self._django_name: other})

    def like(self, pattern: str) -> Q:
        """模糊匹配（不区分大小写），自动去除 ``%`` 通配符。"""
        clean = pattern.strip('%')
        return Q(**{f'{self._django_name}__icontains': clean})

    def contains(self, value: str) -> Q:
        """包含子串（区分大小写）。"""
        return Q(**{f'{self._django_name}__contains': value})

    def ilike(self, pattern: str) -> Q:
        """like() 的别名。"""
        return self.like(pattern)

    def startswith(self, value: str) -> Q:
        """前缀匹配（区分大小写）。"""
        return Q(**{f'{self._django_name}__startswith': value})

    def istartswith(self, value: str) -> Q:
        """前缀匹配（不区分大小写）。"""
        return Q(**{f'{self._django_name}__istartswith': value})

    def endswith(self, value: str) -> Q:
        """后缀匹配（区分大小写）。"""
        return Q(**{f'{self._django_name}__endswith': value})

    def iendswith(self, value: str) -> Q:
        """后缀匹配（不区分大小写）。"""
        return Q(**{f'{self._django_name}__iendswith': value})

    def in_(self, values) -> Q:
        """``field IN (values)``，支持列表和 FluentQuery 子查询。"""
        if hasattr(values, '_qs'):
            return Q(**{f'{self._django_name}__in': values._qs.values(self._django_name)})
        return Q(**{f'{self._django_name}__in': values})

    def between(self, low, high) -> Q:
        """``field BETWEEN low AND high``。"""
        return Q(**{f'{self._django_name}__gte': low}) & Q(**{f'{self._django_name}__lte': high})

    def regex(self, pattern: str) -> Q:
        """正则匹配（区分大小写）。"""
        return Q(**{f'{self._django_name}__regex': pattern})

    def iregex(self, pattern: str) -> Q:
        """正则匹配（不区分大小写）。"""
        return Q(**{f'{self._django_name}__iregex': pattern})

    # ---- 排序辅助 ----

    def desc(self) -> str:
        """返回降序排序字符串 ``'-field_name'``。"""
        return f'-{self._django_name}'

    def asc(self) -> str:
        """返回升序排序字符串 ``'field_name'``。"""
        return self._django_name

    # ---- JSON 字段访问 ----

    def __getitem__(self, key: str) -> _JsonKeyExpression:
        """访问 JSON 字段内的键，如 ``Model.meta['key'] == value``。"""
        return _JsonKeyExpression(self._django_name, key)

    # ---- 显示 ----

    def __repr__(self) -> str:
        return f'FieldExpr({self.model_class.__name__}.{self.field_name})'

    def __str__(self) -> str:
        return self._django_name


class _JsonKeyExpression:
    """JSON 字段键表达式，由 ``Model.json_field['key']`` 产生。"""

    def __init__(self, field_name: str, key: str) -> None:
        self.field_name = field_name
        self.key = key

    def _lookup(self, suffix: str = '') -> str:
        if suffix:
            return f'{self.field_name}__{self.key}__{suffix}'
        return f'{self.field_name}__{self.key}'

    def cast(self, type_hint) -> _CastJsonExpression:
        """将 JSON 值转换为指定类型后再比较。"""
        return _CastJsonExpression(self.field_name, self.key, type_hint)

    def __eq__(self, other) -> Q:  # type: ignore[override]
        return Q(**{self._lookup(): other})

    def __ne__(self, other) -> Q:  # type: ignore[override]
        return ~Q(**{self._lookup(): other})

    def __gt__(self, other) -> Q:
        return Q(**{self._lookup('gt'): other})

    def __ge__(self, other) -> Q:
        return Q(**{self._lookup('gte'): other})

    def __lt__(self, other) -> Q:
        return Q(**{self._lookup('lt'): other})

    def __le__(self, other) -> Q:
        return Q(**{self._lookup('lte'): other})

    def between(self, low, high) -> Q:
        return Q(**{self._lookup('gte'): low}) & Q(**{self._lookup('lte'): high})

    def __repr__(self) -> str:
        return f'JsonKeyExpr({self.field_name}[{self.key!r}])'


class _CastJsonExpression:
    """类型转换后的 JSON 键表达式。"""

    def __init__(self, field_name: str, key: str, type_hint) -> None:
        self.field_name = field_name
        self.key = key
        self.type_hint = type_hint

    def _lookup(self, suffix: str = '') -> str:
        if suffix:
            return f'{self.field_name}__{self.key}__{suffix}'
        return f'{self.field_name}__{self.key}'

    def __eq__(self, other) -> Q:  # type: ignore[override]
        return Q(**{self._lookup(): other})

    def __ne__(self, other) -> Q:  # type: ignore[override]
        return ~Q(**{self._lookup(): other})

    def __gt__(self, other) -> Q:
        return Q(**{self._lookup('gt'): other})

    def __ge__(self, other) -> Q:
        return Q(**{self._lookup('gte'): other})

    def __lt__(self, other) -> Q:
        return Q(**{self._lookup('lt'): other})

    def __le__(self, other) -> Q:
        return Q(**{self._lookup('lte'): other})

    def between(self, low, high) -> Q:
        return Q(**{self._lookup('gte'): low}) & Q(**{self._lookup('lte'): high})

    def __repr__(self) -> str:
        type_name = getattr(self.type_hint, '__name__', str(self.type_hint))
        return f'CastJsonExpr({self.field_name}[{self.key!r}]->{type_name})'


# ---------------------------------------------------------------------------
# QModel 元类与基类
# ---------------------------------------------------------------------------

class QModelBase(models.base.ModelBase):
    """QModel 元类，负责：

    1. 根据 ``GVSDSDK_MANAGE_TABLES`` 设置动态控制 ``managed``
    2. 包装字段描述符，使类级别访问返回 FieldExpression
    """

    def __new__(mcs, name, bases, namespace, **kwargs):
        # 动态设置 managed：子服务器可设 GVSDSDK_MANAGE_TABLES=True 让 gvsdsdk 管理表
        meta = namespace.get('Meta')
        if meta and getattr(meta, 'app_label', None) == 'gvsdsdk':
            try:
                from django.conf import settings
                if getattr(settings, 'GVSDSDK_MANAGE_TABLES', False):
                    meta.managed = True
            except (ImproperlyConfigured, ImportError) as e:
                import logging
                logging.getLogger('gvsdsdk').debug(
                    'QModelBase.__new__: failed to read GVSDSDK_MANAGE_TABLES for %s: %s', name, e,
                )

        cls = super().__new__(mcs, name, bases, namespace, **kwargs)

        # 模型创建后，包装字段描述符以支持类级别 FieldExpression
        if not cls._meta.abstract:
            _wrap_field_descriptors(cls)

        return cls


def _wrap_field_descriptors(cls: Type) -> None:
    """包装 Django 字段描述符，使类级别访问返回 FieldExpression，
    实例级别完全委托给 Django 原生描述符。

    Django 的字段描述符（DeferredAttribute / ForwardManyToOneDescriptor）：
    - ``__get__(None, cls)`` → 返回字段对象本身（类级别）
    - ``__get__(instance, cls)`` → 返回字段值（实例级别）

    我们包装后：
    - ``__get__(None, cls)`` → 返回 FieldExpression（类级别，用于链式 OOP）
    - ``__get__(instance, cls)`` → 委托给原生描述符（实例级别，正常行为）

    兼容性：包装失败时降级到原生描述符，仅记录警告，不中断启动。
    """
    import logging
    _logger = logging.getLogger('gvsdsdk.model_base')

    try:
        from django.db.models.query_utils import DeferredAttribute
        from django.db.models.fields.related_descriptors import (
            ForwardManyToOneDescriptor,
            ForwardOneToOneDescriptor,
        )
        _supported_descriptors = (DeferredAttribute, ForwardManyToOneDescriptor,
                                  ForwardOneToOneDescriptor)
    except ImportError as e:
        _logger.warning('无法导入 Django 描述符类型，跳过字段包装: %s', e)
        return

    for field in cls._meta.local_fields:
        fname = field.name
        if fname.startswith('_'):
            continue

        original_desc = cls.__dict__.get(fname)
        if original_desc is None:
            continue

        # 只包装已知的 Django 描述符类型
        if isinstance(original_desc, _supported_descriptors):
            try:
                wrapper = _FieldExpressionWrapper(original_desc, cls, fname)
                setattr(cls, fname, wrapper)
            except (TypeError, AttributeError) as e:
                _logger.warning('包装字段 %s.%s 失败，使用原生描述符: %s',
                                cls.__name__, fname, e)


class _FieldExpressionWrapper:
    """包装 Django 字段描述符，类级别返回 FieldExpression，实例级别委托给原生描述符。

    这是 QModel 字段表达式机制的核心：当你在类上访问 ``Model.field_name`` 时，
    返回 ``FieldExpression`` 对象，支持 ``Model.field == value`` 等运算符构建 Q 对象；
    当你在实例上访问 ``obj.field_name`` 时，行为与原生 Django 完全一致。
    """

    def __init__(self, original_descriptor, model_class: Type, field_name: str) -> None:
        self._original = original_descriptor
        self._model = model_class
        self._field_name = field_name
        self._has_set: bool = hasattr(original_descriptor, '__set__')

    def __get__(self, obj, objtype=None):
        if obj is None:
            # 类级别访问：返回 FieldExpression
            return FieldExpression(self._model, self._field_name)
        # 实例级别访问：委托给原生 Django 描述符
        return self._original.__get__(obj, objtype)

    def __set__(self, obj, value) -> None:
        if self._has_set:
            # FK/O2O 描述符有 __set__
            self._original.__set__(obj, value)
        else:
            # 普通字段（DeferredAttribute）没有 __set__，直接写 __dict__
            obj.__dict__[self._field_name] = value

    def __repr__(self) -> str:
        return f'_FieldExpressionWrapper({self._model.__name__}.{self._field_name})'


class QModel(models.Model, metaclass=QModelBase):
    """QModel 兼容层基类，提供 SQLAlchemy 风格的链式 OOP 查询接口。

    所有继承 QModel 的模型自动获得：

    - ``Model.query`` : 返回 FluentQuery，支持链式查询
    - ``Model.field_name`` : 类级别访问返回 FieldExpression，支持表达式查询
    - ``Model.field_name`` : 实例级别访问返回字段值（与普通 Django Model 一致）
    - ``Model[condition]`` : 快捷 filter 语法

    用法示例::

        # 链式查询
        orders = Dingdan.query.filter(zhuangtai=8).order_by('-create_time').to_list()

        # 字段表达式
        Dingdan.query.filter(Dingdan.jine > 100, Dingdan.zhuangtai.in_([1,2,3]))

        # 聚合
        Dingdan.query.aggregate(total=Sum('jine'))

        # values / annotate
        Dingdan.query.values('zhuangtai').annotate(cnt=Count('id'))

        # 分页
        Dingdan.query.paginate(page=1, per_page=20)

        # 快捷 filter
        Dingdan[Dingdan.zhuangtai == 8].order_by('-create_time').to_list()
    """

    class Meta:
        abstract = True

    @classproperty
    def query(cls) -> FluentQuery[Self]:
        """返回 FluentQuery 兼容层查询构建器，支持链式 OOP 查询。"""
        return FluentQuery(cls)

    @classmethod
    def __class_getitem__(cls, item) -> FluentQuery[Self]:
        """支持 ``Model[condition]`` 语法，返回 filter 后的 FluentQuery。

        用法::

            Dingdan[Dingdan.zhuangtai == 8].order_by('-create_time').to_list()
            Dingdan[{'zhuangtai': 8}].to_list()
        """
        if isinstance(item, Q):
            return cls.query.filter(item)
        if isinstance(item, dict):
            return cls.query.filter(**item)
        return cls.query.filter(item)

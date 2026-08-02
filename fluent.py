"""FluentQuery 兼容层查询构建器

提供 SQLAlchemy 风格的链式 OOP 查询接口，包装 Django QuerySet。

核心类：
- ``FluentQuery`` — 链式查询构建器，由 ``Model.query`` 返回
- ``Session`` — 简易会话管理（add/commit/rollback）
- ``_CompatFunc`` — 兼容层聚合函数（func.sum / func.count 等）
- ``db`` — 全局数据库操作入口（db.session / db.or_ / db.and_）

典型用法::

    from gvsdsdk.fluent import db, func, FQ

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

    # 兼容层聚合函数
    func.sum(Dingdan.jine)
    func.count(Dingdan.id)
    func.year(Dingdan.create_time)
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Generic, Iterator, List, Optional, Sequence, Tuple, Type, TypeVar, Union

from django.core.exceptions import FieldDoesNotExist
from django.db import connection as default_db_connection, models, transaction
from django.db.models import Q, Count, Sum, Avg, Min, Max

logger = logging.getLogger(__name__)

_local = threading.local()

#: 模型类型变量，用于 FluentQuery[_M] 泛型参数
_M = TypeVar('_M', bound=models.Model)


def _get_field_expression_cls():
    """延迟导入 FieldExpression，避免 fluent ↔ model_base 循环依赖。"""
    from gvsdsdk.model_base import FieldExpression
    return FieldExpression


# ---------------------------------------------------------------------------
# 兼容层聚合函数
# ---------------------------------------------------------------------------

class _AggregateExpr:
    """聚合表达式包装，关联模型类和 Django Aggregate 对象。"""

    def __init__(self, model: Optional[Type[models.Model]], aggregate: models.Aggregate) -> None:
        self.model = model
        self.aggregate = aggregate


class _ExtractExpr:
    """日期提取表达式（year/month/day），用于 ``func.year(field) == 2024`` 等。"""

    def __init__(self, field_name: str, lookup_type: str) -> None:
        self.field_name = field_name
        self.lookup_type = lookup_type

    def __eq__(self, other) -> Q:  # type: ignore[override]
        return Q(**{f'{self.field_name}__{self.lookup_type}': other})

    def __and__(self, other) -> Q:
        if isinstance(other, Q):
            return Q(**{f'{self.field_name}__{self.lookup_type}': None}) & other
        return self.__eq__(other)


class _CompatFunc:
    """兼容层聚合函数集合，提供 SQLAlchemy 风格的 ``func.sum()`` / ``func.count()`` 等。

    用法::

        from gvsdsdk.fluent import func

        func.sum(Model.jine)       → Sum('jine')
        func.count(Model.id)       → Count('id')
        func.max(Model.jine)       → Max('jine')
        func.min(Model.jine)       → Min('jine')
        func.avg(Model.jine)       → Avg('jine')
        func.year(Model.riqi)      → _ExtractExpr('riqi', 'year')
        func.month(Model.riqi)     → _ExtractExpr('riqi', 'month')
        func.day(Model.riqi)       → _ExtractExpr('riqi', 'day')
    """

    def sum(self, field_expr) -> Union[_AggregateExpr, Sum]:
        """求和聚合。"""
        name = self._resolve_name(field_expr)
        agg = Sum(name)
        model = getattr(field_expr, 'model_class', None)
        return _AggregateExpr(model, agg) if model else agg

    def count(self, field_expr=None) -> Union[_AggregateExpr, Count]:
        """计数聚合，无参数时等价于 COUNT(*)。"""
        if field_expr is None:
            return Count('*')
        name = self._resolve_name(field_expr)
        agg = Count(name)
        model = getattr(field_expr, 'model_class', None)
        return _AggregateExpr(model, agg) if model else agg

    def max(self, field_expr) -> Union[_AggregateExpr, Max]:
        """最大值聚合。"""
        name = self._resolve_name(field_expr)
        agg = Max(name)
        model = getattr(field_expr, 'model_class', None)
        return _AggregateExpr(model, agg) if model else agg

    def min(self, field_expr) -> Union[_AggregateExpr, Min]:
        """最小值聚合。"""
        name = self._resolve_name(field_expr)
        agg = Min(name)
        model = getattr(field_expr, 'model_class', None)
        return _AggregateExpr(model, agg) if model else agg

    def avg(self, field_expr) -> Union[_AggregateExpr, Avg]:
        """平均值聚合。"""
        name = self._resolve_name(field_expr)
        agg = Avg(name)
        model = getattr(field_expr, 'model_class', None)
        return _AggregateExpr(model, agg) if model else agg

    def year(self, field_expr) -> _ExtractExpr:
        """提取年份，用于 ``func.year(Model.date) == 2024``。"""
        return _ExtractExpr(self._resolve_name(field_expr), 'year')

    def month(self, field_expr) -> _ExtractExpr:
        """提取月份。"""
        return _ExtractExpr(self._resolve_name(field_expr), 'month')

    def day(self, field_expr) -> _ExtractExpr:
        """提取日期。"""
        return _ExtractExpr(self._resolve_name(field_expr), 'day')

    @staticmethod
    def _resolve_name(field_expr) -> str:
        """从 FieldExpression 或字符串解析 Django 字段名。"""
        if hasattr(field_expr, '_django_name'):
            return field_expr._django_name
        if hasattr(field_expr, 'field_name'):
            return field_expr.field_name
        return str(field_expr)


func = _CompatFunc()


# ---------------------------------------------------------------------------
# DELETE 语句构建器
# ---------------------------------------------------------------------------

class _DeleteStmt:
    """DELETE 语句构建器，支持 ``Model.query.filter(...).delete()`` 风格。"""

    def __init__(self, model_class: Type[models.Model], conditions: Optional[Q] = None) -> None:
        self.model_class = model_class
        self.conditions = conditions or Q()

    def where(self, *args: Q, **kwargs: Any) -> _DeleteStmt:
        """添加 WHERE 条件。"""
        new_conditions = Q()
        for a in args:
            if isinstance(a, Q):
                new_conditions &= a
        if kwargs:
            new_conditions &= Q(**kwargs)
        self.conditions = new_conditions
        return self

    def execute(self) -> Tuple[int, Dict[str, int]]:
        """执行删除，返回 (删除数, {表名: 删除数})。"""
        if self.model_class and self.conditions:
            result = self.model_class.objects.filter(self.conditions).delete()
        elif self.model_class:
            result = self.model_class.objects.all().delete()
        else:
            return (0, {})
        _invalidate_cache()
        return result


# ---------------------------------------------------------------------------
# 会话与缓存
# ---------------------------------------------------------------------------

def _get_session() -> Session:
    """获取当前线程的 Session 实例。"""
    if not hasattr(_local, 'db_session'):
        _local.db_session = Session()
    return _local.db_session


def _get_cache() -> Dict:
    """获取当前线程的查询缓存。"""
    if not hasattr(_local, 'query_cache'):
        _local.query_cache = {}
    return _local.query_cache


def _invalidate_cache() -> None:
    """清除当前线程的查询缓存。"""
    if hasattr(_local, 'query_cache'):
        _local.query_cache.clear()


def _cleanup_thread_local(**kwargs):
    """清理当前线程的 Session 和缓存，防止长运行线程内存泄漏。
    注册到 Django request_finished 信号，每个请求结束时自动调用。
    """
    if hasattr(_local, 'db_session'):
        session = _local.db_session
        try:
            session.rollback()
        except Exception:
            pass
        del _local.db_session
    _invalidate_cache()
    if hasattr(_local, 'query_cache'):
        del _local.query_cache


# 注册请求结束信号清理（防止 gunicorn/celery worker 内存泄漏）
try:
    from django.core.signals import request_finished as _request_finished
    _request_finished.connect(_cleanup_thread_local, dispatch_uid='gvsdsdk_fluent_cleanup')
except Exception:
    pass


# ---------------------------------------------------------------------------
# 关联字段访问器
# ---------------------------------------------------------------------------

class RelationAccessor:
    """关联字段访问器，支持 ``Model.relation.field`` 跨表字段表达式。

    用法::

        # 自动解析关联字段
        Dingdan.query.filter(Dingdan.shangjia.nicheng == '测试')
    """

    def __init__(self, model_class: Type[models.Model], prefix: str = '') -> None:
        self._model = model_class
        self._prefix = prefix

    def __getattr__(self, name: str):
        if name.startswith('_'):
            return object.__getattribute__(self, name)
        try:
            self._model._meta.get_field(name)
            full_name = f'{self._prefix}__{name}' if self._prefix else name
            return _get_field_expression_cls()(self._model, full_name)
        except (FieldDoesNotExist, AttributeError):
            for rel in self._model._meta.get_fields(include_hidden=True):
                if hasattr(rel, 'related_model') and rel.related_model:
                    rel_name = ''
                    if hasattr(rel, 'get_accessor_name'):
                        rel_name = rel.get_accessor_name()
                    elif hasattr(rel, 'name'):
                        rel_name = rel.name
                    if rel_name and rel_name == name:
                        full_prefix = f'{self._prefix}__{rel_name}' if self._prefix else rel_name
                        return RelationAccessor(rel.related_model, full_prefix)
            return _get_field_expression_cls()(self._model, name)

    def __repr__(self) -> str:
        return f'RelationAccessor({self._model.__name__}, prefix={self._prefix!r})'


# ---------------------------------------------------------------------------
# Values 结果集
# ---------------------------------------------------------------------------

class _ValuesResult:
    """包装 Django ValuesQuerySet，支持链式调用。

    底层使用 Django ``.values()`` 返回字典，保持与 Django 行为一致。
    额外提供 ``.all()`` 返回字典列表，``.first()`` 返回字典或 None，
    ``.scalar()`` 返回单字段场景的标量值。
    """

    def __init__(self, qs: models.QuerySet, fields: List[str], original_fields: Tuple) -> None:
        self._qs: models.QuerySet = qs
        self._fields: List[str] = fields
        self._original_fields: Tuple = original_fields

    @property
    def query(self) -> Any:
        """暴露底层 Django QuerySet 的 query 属性，支持 Subquery() 等场景。"""
        return self._qs.query

    def filter(self, *args: Any, **kwargs: Any) -> _ValuesResult:
        self._qs = self._qs.filter(*args, **kwargs)
        return self

    def exclude(self, *args: Any, **kwargs: Any) -> _ValuesResult:
        self._qs = self._qs.exclude(*args, **kwargs)
        return self

    def order_by(self, *fields: str) -> _ValuesResult:
        self._qs = self._qs.order_by(*fields)
        return self

    def distinct(self) -> _ValuesResult:
        self._qs = self._qs.distinct()
        return self

    def annotate(self, **kwargs: Any) -> _ValuesResult:
        self._qs = self._qs.annotate(**kwargs)
        return self

    def first(self) -> Optional[Dict[str, Any]]:
        """返回第一条记录（字典）或 None。"""
        return self._qs.first()

    def all(self) -> List[Dict[str, Any]]:
        """返回所有记录的字典列表。"""
        return list(self._qs)

    def scalar(self) -> Any:
        """返回单字段场景的标量值。"""
        row = self._qs.first()
        if row is None:
            return None
        if self._fields:
            return row.get(self._fields[0])
        return row

    def count(self) -> int:
        return self._qs.count()

    def exists(self) -> bool:
        return self._qs.exists()

    def __getitem__(self, key: Union[int, slice]) -> Any:
        if isinstance(key, slice):
            self._qs = self._qs[key]
            return self
        else:
            return self._qs[key]

    def __iter__(self) -> Iterator[Dict[str, Any]]:
        return iter(self._qs)

    def __len__(self) -> int:
        return self.count()

    def __bool__(self) -> bool:
        return self._qs.exists()

    def __repr__(self) -> str:
        return f'_ValuesResult(fields={self._fields}, count={self.count()})'


# ---------------------------------------------------------------------------
# FluentQuery 核心查询构建器
# ---------------------------------------------------------------------------

class FluentQuery(Generic[_M]):
    """QModel 兼容层查询构建器，提供 SQLAlchemy 风格的链式 OOP 查询接口。

    由 ``Model.query`` 返回，支持链式调用::

        # 基础查询
        Dingdan.query.filter(zhuangtai=8).all()

        # 链式过滤
        Dingdan.query.filter(jine__gt=100).order_by('-create_time').limit(10)

        # 字段表达式
        Dingdan.query.filter(Dingdan.jine > 100, Dingdan.zhuangtai.in_([1,2,3]))

        # 聚合
        Dingdan.query.aggregate(total=Sum('jine'))

        # values / annotate
        Dingdan.query.values('zhuangtai').annotate(cnt=Count('id'))

        # 分页
        Dingdan.query.paginate(page=1, per_page=20)

    泛型参数 ``_M`` 携带模型类型，使 IDE 能推断链式调用返回的模型实例类型。
    """

    def __init__(self, model_class: Type[_M]) -> None:
        self._model: Type[_M] = model_class
        self._qs: models.QuerySet = model_class.objects.all()
        self._joined_models: List[Type[models.Model]] = []
        self._cache_enabled: bool = True

    @property
    def query(self) -> Any:
        """暴露底层 Django QuerySet 的 query 属性，支持 Subquery() 等场景。"""
        return self._qs.query

    @property
    def model(self) -> Type[_M]:
        """返回关联的模型类。"""
        return self._model

    def _clone(self) -> FluentQuery[_M]:
        """克隆当前查询状态。"""
        q = FluentQuery(self._model)
        q._qs = self._qs
        q._joined_models = list(self._joined_models)
        q._cache_enabled = self._cache_enabled
        return q

    def _cache_key(self, method: str) -> Tuple[Any, str, Any]:
        """生成缓存键。

        使用 hash() 而非 str() 避免 Django QuerySet 的 SQL 编译开销。
        Django Query 未自定义 __hash__，使用 object 默认实现（基于 id），
        在同一 FluentQuery 实例内多次调用 first()/to_list()/count() 时
        缓存键稳定，可正常命中。
        """
        try:
            query_hash = hash(self._qs.query)
        except Exception:
            query_hash = id(self._qs)
        return (self._model, method, query_hash)

    def _remap_q(self, q: Q) -> Q:
        """重映射 Q 对象中的字段名（用于 join 跨表查询）。"""
        new_children: List[Any] = []
        for child in q.children:
            if isinstance(child, Q):
                new_children.append(self._remap_q(child))
            elif isinstance(child, tuple):
                key, val = child
                new_key = self._remap_key(key)
                new_children.append((new_key, val))
            else:
                new_children.append(child)
        if new_children:
            new_q = Q(*new_children)
            new_q.connector = q.connector
            new_q.negated = q.negated
            return new_q
        return q

    def _remap_key(self, key: str) -> str:
        """重映射字段名（用于 join 跨表查询自动添加关联前缀）。"""
        parts = key.split('__')
        field_name = parts[0]
        try:
            self._model._meta.get_field(field_name)
            return key
        except FieldDoesNotExist:
            pass
        for joined in self._joined_models:
            try:
                joined._meta.get_field(field_name)
                for rel in self._model._meta.get_fields(include_hidden=True):
                    if hasattr(rel, 'related_model') and rel.related_model == joined:
                        acc = ''
                        if hasattr(rel, 'get_accessor_name'):
                            acc = rel.get_accessor_name()
                        elif hasattr(rel, 'name'):
                            acc = rel.name
                        if acc and acc != '+':
                            return acc + '__' + key
                return joined._meta.model_name + '__' + key
            except FieldDoesNotExist:
                pass
        return key

    # ---- 过滤 ----

    def filter(self, *args: Any, **kwargs: Any) -> FluentQuery[_M]:
        """过滤查询，支持 Q 对象和关键字参数；返回新 FluentQuery，不修改原查询。"""
        converted_args: List[Any] = []
        for arg in args:
            if isinstance(arg, Q) and self._joined_models:
                converted_args.append(self._remap_q(arg))
            else:
                converted_args.append(arg)
        q = self._clone()
        q._qs = self._qs.filter(*converted_args, **kwargs)
        return q

    def exclude(self, *args: Any, **kwargs: Any) -> FluentQuery[_M]:
        """排除查询；返回新 FluentQuery，不修改原查询。"""
        converted_args: List[Any] = []
        for arg in args:
            if isinstance(arg, Q) and self._joined_models:
                converted_args.append(self._remap_q(arg))
            else:
                converted_args.append(arg)
        q = self._clone()
        q._qs = self._qs.exclude(*converted_args, **kwargs)
        return q

    # ---- 排序 ----

    def order_by(self, *args: Any) -> FluentQuery[_M]:
        """排序，支持字符串和 FieldExpression；返回新 FluentQuery，不修改原查询。"""
        converted: List[str] = []
        for a in args:
            if isinstance(a, str):
                converted.append(a)
            elif isinstance(a, _get_field_expression_cls()):
                converted.append(str(a))
            else:
                converted.append(str(a))
        q = self._clone()
        q._qs = q._qs.order_by(*converted)
        return q

    # ---- 获取记录 ----

    def first(self) -> Optional[_M]:
        """返回第一条记录或 None。"""
        if self._cache_enabled:
            cache = _get_cache()
            key = self._cache_key('first')
            if key in cache:
                return cache[key]
        obj = self._qs.first()
        if obj is not None:
            _get_session().track(obj)
        if self._cache_enabled:
            _get_cache()[self._cache_key('first')] = obj
        return obj

    def last(self) -> Optional[_M]:
        """返回最后一条记录或 None。"""
        return self._qs.last()

    def get(self, *args: Any, **kwargs: Any) -> _M:
        """获取唯一匹配记录，不存在或多个时抛异常。"""
        if args:
            obj = self._qs.filter(*args).get(**kwargs)
        else:
            obj = self._qs.get(**kwargs)
        _get_session().track(obj)
        return obj

    def all(self) -> FluentQuery[_M]:
        """返回新实例以支持链式调用（与 Django QuerySet.all() 一致）。
        如需获取列表，使用 ``.to_list()`` 或 ``list()``。"""
        q = self._clone()
        q._qs = q._qs.all()
        return q

    def to_list(self) -> List[_M]:
        """执行查询并返回模型实例列表。"""
        if self._cache_enabled:
            cache = _get_cache()
            key = self._cache_key('to_list')
            if key in cache:
                return list(cache[key])  # 返回副本，防止调用方 mutate 污染缓存
        results = list(self._qs)
        session = _get_session()
        for obj in results:
            session.track(obj)
        if self._cache_enabled:
            _get_cache()[self._cache_key('to_list')] = results
        return list(results)  # 返回副本，保护缓存中的原始列表

    def count(self) -> int:
        """返回匹配记录数。"""
        if self._cache_enabled:
            cache = _get_cache()
            key = self._cache_key('count')
            if key in cache:
                return cache[key]
        result = self._qs.count()
        if self._cache_enabled:
            _get_cache()[self._cache_key('count')] = result
        return result

    def exists(self) -> bool:
        """判断是否存在匹配记录。"""
        return self._qs.exists()

    # ---- 创建/更新/删除 ----

    def create(self, **kwargs: Any) -> _M:
        """创建并保存新记录。"""
        obj = self._model.objects.create(**kwargs)
        _invalidate_cache()
        return obj

    def get_or_create(self, defaults: Optional[Dict[str, Any]] = None,
                      **kwargs: Any) -> Tuple[_M, bool]:
        """查询或创建记录，返回 ``(obj, created)`` 元组。"""
        obj, created = self._model.objects.get_or_create(defaults=defaults, **kwargs)
        if created:
            _invalidate_cache()
        _get_session().track(obj)
        return obj, created

    def update_or_create(self, defaults: Optional[Dict[str, Any]] = None,
                         **kwargs: Any) -> Tuple[_M, bool]:
        """更新或创建记录，返回 ``(obj, created)`` 元组。"""
        obj, created = self._model.objects.update_or_create(defaults=defaults, **kwargs)
        _invalidate_cache()
        _get_session().track(obj)
        return obj, created

    def bulk_create(self, objs: List[_M], batch_size: Optional[int] = None,
                    ignore_conflicts: bool = False) -> List[_M]:
        """批量创建记录。"""
        result = self._model.objects.bulk_create(
            objs, batch_size=batch_size, ignore_conflicts=ignore_conflicts
        )
        _invalidate_cache()
        return result

    def bulk_update(self, objs: List[_M], fields: List[str],
                    batch_size: Optional[int] = None) -> int:
        """批量更新记录，返回受影响行数。"""
        result = self._model.objects.bulk_update(objs, fields, batch_size=batch_size)
        _invalidate_cache()
        return result

    def update(self, **kwargs: Any) -> int:
        """批量更新匹配记录，返回受影响行数。"""
        result = self._qs.update(**kwargs)
        _invalidate_cache()
        return result

    def delete(self) -> Tuple[int, Dict[str, int]]:
        """批量删除匹配记录，返回 ``(删除数, {表名: 删除数})``。"""
        result = self._qs.delete()
        _invalidate_cache()
        return result

    # ---- 查询修饰 ----

    def none(self) -> FluentQuery[_M]:
        """返回空结果集，返回新实例以支持链式调用。"""
        q = self._clone()
        q._qs = q._qs.none()
        return q

    def limit(self, n: int) -> FluentQuery[_M]:
        """限制返回记录数，返回新实例以支持链式调用。"""
        q = self._clone()
        q._qs = q._qs[:n]
        return q

    def offset(self, n: int) -> FluentQuery[_M]:
        """跳过前 n 条记录，返回新实例以支持链式调用。"""
        q = self._clone()
        q._qs = q._qs[n:]
        return q

    def join(self, *args: Type[models.Model]) -> FluentQuery[_M]:
        """关联模型，用于跨表查询自动映射字段，返回新实例以支持链式调用。"""
        q = self._clone()
        for arg in args:
            if isinstance(arg, type) and issubclass(arg, models.Model):
                if arg not in q._joined_models:
                    q._joined_models.append(arg)
        return q

    def select_related(self, *fields: str) -> FluentQuery[_M]:
        """外键关联查询优化，返回新实例以支持链式调用。"""
        q = self._clone()
        q._qs = q._qs.select_related(*fields)
        return q

    def prefetch_related(self, *fields: str) -> FluentQuery[_M]:
        """多对多/反向关联查询优化，返回新实例以支持链式调用。"""
        q = self._clone()
        q._qs = q._qs.prefetch_related(*fields)
        return q

    def only(self, *fields: str) -> FluentQuery[_M]:
        """只加载指定字段，返回新实例以支持链式调用。"""
        q = self._clone()
        q._qs = q._qs.only(*fields)
        return q

    def defer(self, *fields: str) -> FluentQuery[_M]:
        """延迟加载指定字段，返回新实例以支持链式调用。"""
        q = self._clone()
        q._qs = q._qs.defer(*fields)
        return q

    def select_for_update(self, nowait: bool = False, skip_locked: bool = False,
                          of: Tuple = (), no_key: bool = False) -> FluentQuery[_M]:
        """行级锁，返回新实例以支持链式调用。"""
        q = self._clone()
        q._qs = q._qs.select_for_update(
            nowait=nowait, skip_locked=skip_locked, of=of, no_key=no_key
        )
        return q

    def distinct(self) -> FluentQuery[_M]:
        """去重查询，返回新实例以支持链式调用。"""
        q = self._clone()
        q._qs = q._qs.distinct()
        return q

    def using(self, alias: str) -> FluentQuery[_M]:
        """指定数据库别名，返回新实例以支持链式调用。"""
        q = self._clone()
        q._qs = q._qs.using(alias)
        return q

    def no_cache(self) -> FluentQuery[_M]:
        """禁用查询缓存，返回新实例以支持链式调用。"""
        q = self._clone()
        q._cache_enabled = False
        return q

    def params(self, **kwargs: Any) -> FluentQuery[_M]:
        """参数占位（SQLAlchemy 兼容），返回新实例以支持链式调用。"""
        return self._clone()

    # ---- 聚合与注解 ----

    def annotate(self, **kwargs: Any) -> FluentQuery[_M]:
        """注解查询，添加聚合/计算字段，返回新实例以支持链式调用。"""
        q = self._clone()
        q._qs = q._qs.annotate(**kwargs)
        return q

    def aggregate(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        """聚合查询，返回聚合结果字典。

        兼容 Django QuerySet.aggregate() 的两种调用方式：
            aggregate(Max('field'))            # 位置参数
            aggregate(field__max=Max('field')) # 关键字参数
        """
        return self._qs.aggregate(*args, **kwargs)

    def values(self, *fields: Union[str, _AggregateExpr, models.Expression]) -> _ValuesResult:
        """返回指定字段的字典结果，支持链式调用。"""
        if not fields:
            return _ValuesResult(self._qs.values(), [], fields)

        annotations: Dict[str, models.Aggregate] = {}
        converted: List[str] = []
        for i, f in enumerate(fields):
            if isinstance(f, _AggregateExpr):
                annotations[f'val{i}'] = f.aggregate
                converted.append(f'val{i}')
            elif isinstance(f, (models.Aggregate, models.Expression)):
                annotations[f'val{i}'] = f
                converted.append(f'val{i}')
            elif isinstance(f, str):
                converted.append(f)
            else:
                converted.append(str(f))

        qs = self._qs
        if annotations:
            qs = qs.annotate(**annotations)
        qs = qs.values(*converted)
        return _ValuesResult(qs, converted, fields)

    def with_entities(self, *fields: Union[str, _AggregateExpr, models.Expression]) -> _ValuesResult:
        """values() 的别名，SQLAlchemy 风格。"""
        return self.values(*fields)

    def values_list(self, *fields: str, flat: bool = False) -> Any:
        """返回指定字段的元组列表。"""
        return self._qs.values_list(*fields, flat=flat)

    # ---- 分页 ----

    def paginate(self, page: int = 1, per_page: int = 20,
                 error_out: bool = False) -> Any:
        """分页查询，返回 Page 对象（含 items, total, pages 等属性）。"""
        from django.core.paginator import Paginator, EmptyPage
        if not self._qs.ordered:
            meta = getattr(self._model, '_meta', None)
            if meta and meta.pk:
                self._qs = self._qs.order_by(meta.pk.name)
        paginator = Paginator(self._qs, per_page)
        try:
            page_obj = paginator.page(page)
        except EmptyPage:
            page_obj = paginator.page(1)
        page_obj.total = paginator.count
        page_obj.items = page_obj.object_list
        page_obj.pages = paginator.num_pages
        page_obj.page = page
        page_obj.per_page = per_page
        # 使用 PascalCase 属性名，避免覆盖 Django Page 原生 has_next()/has_previous() 方法
        page_obj.HasNext = page_obj.has_next()
        page_obj.HasPrev = page_obj.has_previous()
        page_obj.NextNum = page_obj.next_page_number() if page_obj.HasNext else None
        page_obj.PrevNum = page_obj.previous_page_number() if page_obj.HasPrev else None
        return page_obj

    # ---- 魔术方法 ----

    def __getitem__(self, key: Union[int, slice]) -> Any:
        """支持切片 ``[start:end]`` 和索引 ``[n]``。切片返回 self，索引返回模型实例。"""
        if isinstance(key, slice):
            self._qs = self._qs[key]
            return self
        else:
            return self._qs[key]

    def __iter__(self) -> Iterator[_M]:
        return iter(self._qs)

    def __len__(self) -> int:
        return self.count()

    def __bool__(self) -> bool:
        return self.exists()

    def __repr__(self) -> str:
        return f'FluentQuery({self._model.__name__})'


# ---------------------------------------------------------------------------
# Session 会话管理
# ---------------------------------------------------------------------------

class Session:
    """简易会话管理器，支持 add/delete/commit/rollback 操作。

    用法::

        session = db.session
        session.add(obj)
        session.commit()
    """

    def __init__(self) -> None:
        self._pending_adds: List[models.Model] = []
        self._pending_deletes: List[models.Model] = []
        self._tracked: set = set()

    def add(self, obj: models.Model) -> None:
        """添加对象到待保存列表。"""
        self._pending_adds.append(obj)

    def delete(self, obj: models.Model) -> None:
        """添加对象到待删除列表。"""
        self._pending_deletes.append(obj)
        self._tracked.discard(obj)

    def track(self, obj: models.Model) -> None:
        """跟踪对象变更（commit 时自动 save）。"""
        if isinstance(obj, models.Model):
            self._tracked.add(obj)

    def commit(self) -> None:
        """提交所有待处理操作（删除 → 新增 → 更新）。"""
        delete_pks = {(obj.__class__, obj.pk) for obj in self._pending_deletes
                      if obj.pk is not None}
        with transaction.atomic():
            for obj in self._pending_deletes:
                obj.delete()
            for obj in self._pending_adds:
                obj.save()
            for obj in self._tracked:
                if obj.pk is not None and (obj.__class__, obj.pk) in delete_pks:
                    continue
                # 注意：此处未使用 update_fields，并发场景可能产生 lost update。
                # 需要精确更新时，应直接在业务代码中使用 obj.save(update_fields=[...])
                try:
                    obj.save()
                except Exception:
                    logger.exception('Session.commit: skip tracked obj %s', obj)
                    raise  # 让 atomic 回滚整个事务，避免部分保存数据一致性破坏
        self._pending_adds.clear()
        self._pending_deletes.clear()
        self._tracked.clear()
        _invalidate_cache()

    def rollback(self) -> None:
        """回滚所有待处理操作。"""
        self._pending_adds.clear()
        self._pending_deletes.clear()
        self._tracked.clear()
        _invalidate_cache()

    def query(self, *args) -> Optional[FluentQuery[Any]]:
        """创建查询构建器，支持传入模型类或聚合表达式。"""
        model: Optional[Type[models.Model]] = None
        aggregates: List = []
        for a in args:
            if isinstance(a, type) and issubclass(a, models.Model):
                model = a
            elif isinstance(a, _AggregateExpr):
                if a.model:
                    model = a.model
                aggregates.append(a.aggregate)
            elif isinstance(a, (models.Aggregate, models.Expression)):
                aggregates.append(a)
        if not aggregates and model:
            return FluentQuery(model)
        if aggregates and model:
            fq = FluentQuery(model)
            return fq.values(*args)
        return None

    def flush(self) -> None:
        """flush() 的别名，等同于 commit()。"""
        self.commit()

    def execute(self, stmt, params=None) -> None:
        """执行语句（支持 _DeleteStmt 和参数化 SQL）。

        安全：字符串 SQL 必须配合 params 参数化，防止 SQL 注入。
        缓存：执行写操作后自动失效线程级查询缓存。
        """
        if isinstance(stmt, _DeleteStmt):
            stmt.execute()  # _DeleteStmt.execute() 内部已调用 _invalidate_cache()
        elif isinstance(stmt, str):
            if params is None:
                raise ValueError(
                    'execute() 拒绝执行无参数化的原始 SQL 字符串，'
                    '请使用 execute(sql, params) 参数化查询或使用 _DeleteStmt'
                )
            connection = default_db_connection
            with connection.cursor() as cursor:
                cursor.execute(stmt, params)
            _invalidate_cache()
        else:
            raise TypeError(f'execute() 不支持类型: {type(stmt).__name__}')


# ---------------------------------------------------------------------------
# 全局入口
# ---------------------------------------------------------------------------

class DB:
    """全局数据库操作入口。

    用法::

        from gvsdsdk.fluent import db

        session = db.session
        q = db.or_(Model.field == 1, Model.field == 2)
        q = db.and_(Model.field > 0, Model.field < 100)
    """

    @property
    def session(self) -> Session:
        """获取当前线程的 Session 实例。"""
        return _get_session()

    @staticmethod
    def or_(*args: Q) -> Q:
        """逻辑 OR，合并多个 Q 对象。"""
        result: Optional[Q] = None
        for q in args:
            if isinstance(q, Q):
                if result is None:
                    result = q
                else:
                    result |= q
        return result if result is not None else Q()

    @staticmethod
    def and_(*args: Q) -> Q:
        """逻辑 AND，合并多个 Q 对象。"""
        result = Q()
        for q in args:
            if isinstance(q, Q):
                result &= q
        return result


db = DB()


def FQ(model_class: Type[_M]) -> FluentQuery[_M]:
    """FluentQuery 工厂函数，等同于 ``Model.query``。"""
    return FluentQuery(model_class)

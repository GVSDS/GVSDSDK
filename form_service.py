"""模块：通用 FormService 引擎

提供子服务器开箱即用的 ORM 表管理 API。
子服务器只需继承 FormServiceBase 并注册 TABLE_MAP 即可。

使用方式：
    # 子服务器 form_service.py
    from gvsdsdk.form_service import FormServiceBase
    from .models import MyModel1, MyModel2

    class FormService(FormServiceBase):
        TABLE_MAP = {
            'MyModel1': MyModel1,
            'MyModel2': MyModel2,
        }

    # 子服务器 views.py
    from gvsdsdk.form_service import FormServiceAPIView
    from .form_service import FormService
"""

import uuid
import datetime
import logging
from django.db import DatabaseError
from django.db.models import Q

logger = logging.getLogger(__name__)


def _PascalizeRecord(record, model=None):
    """将数据库记录的键名转为 PascalCase（使用 db_column）"""
    result = {}
    column_map = {}
    if model is not None:
        for f in model._meta.get_fields():
            if hasattr(f, 'db_column') and f.db_column:
                column_map[f.name] = f.db_column
                if hasattr(f, 'attname') and f.attname != f.name:
                    column_map[f.attname] = f.db_column
    for key, val in record.items():
        out_key = column_map.get(key, key)
        if isinstance(val, bytes) and len(val) == 16:
            try:
                val = str(uuid.UUID(bytes=val))
            except ValueError:
                pass
        elif isinstance(val, datetime.datetime):
            val = val.strftime('%Y-%m-%d %H:%M:%S')
        elif isinstance(val, datetime.date):
            val = val.isoformat()
        result[out_key] = val
    return result


class FormServiceBase:
    """通用 FormService 基类

    子类需要定义：
        TABLE_MAP: dict — 表名到 Django Model 的映射
        ENTITY_PICKER_MAP: dict — ForeignKey 到实体选择器的映射（可选）
        READONLY_TABLES_FOR_NORMAL: set — 普通用户只读的表（可选）
        ADMIN_ONLY_TABLES: set — 仅管理员可访问的表（可选）
    """

    TABLE_MAP = {}
    ENTITY_PICKER_MAP = {}
    READONLY_TABLES_FOR_NORMAL = set()
    ADMIN_ONLY_TABLES = set()

    def __init__(self, user=None):
        self.user = user

    def _BuildFieldMap(self, model):
        pascal_map = {}
        for f in model._meta.get_fields():
            if hasattr(f, 'name'):
                pascal_map[f.name] = f
        return pascal_map

    def _ResolveFieldName(self, key, pascal_map):
        if key in pascal_map:
            return pascal_map[key].name
        return None

    def _ConvertValueForField(self, field, val):
        if val is None or val == '':
            if getattr(field, 'null', False):
                return None
            return val
        field_type = field.__class__.__name__
        if field_type == 'UUIDField' and isinstance(val, str):
            try:
                return uuid.UUID(val).bytes
            except ValueError:
                return val
        if field_type == 'BinaryField' and isinstance(val, str):
            try:
                return uuid.UUID(val).bytes
            except ValueError:
                return val
        if field_type == 'ForeignKey' and isinstance(val, str):
            try:
                return uuid.UUID(val).bytes
            except ValueError:
                return val
        if field_type in ('SmallIntegerField', 'IntegerField', 'BigIntegerField',
                          'PositiveIntegerField', 'PositiveSmallIntegerField',
                          'FloatField', 'DecimalField') and isinstance(val, str):
            try:
                if field_type == 'FloatField':
                    return float(val)
                return int(val)
            except (ValueError, TypeError):
                return val
        if field_type == 'DateTimeField' and isinstance(val, str):
            try:
                return datetime.datetime.fromisoformat(val)
            except (ValueError, TypeError):
                return val
        if hasattr(field, 'max_length') and field.max_length is not None and isinstance(val, str) and len(val) > field.max_length:
            logger.warning('字段 %s 值超长（%d > %d），已截断', getattr(field, 'name', '?'), len(val), field.max_length)
            val = val[:field.max_length]
        return val

    @staticmethod
    def _MapFieldType(field):
        t = field.__class__.__name__
        mapping = {
            'UUIDField': 'uuid',
            'BinaryField': 'uuid',
            'FixedBinaryField': 'uuid',
            'CharField': 'text',
            'TextField': 'textarea',
            'JSONField': 'textarea',
            'SmallIntegerField': 'number',
            'IntegerField': 'number',
            'BigIntegerField': 'number',
            'PositiveIntegerField': 'number',
            'PositiveSmallIntegerField': 'number',
            'FloatField': 'number',
            'DecimalField': 'number',
            'BooleanField': 'boolean',
            'NullBooleanField': 'boolean',
            'DateTimeField': 'datetime',
            'DateField': 'date',
            'TimeField': 'time',
            'ForeignKey': 'uuid',
            'OneToOneField': 'uuid',
            'ManyToManyField': 'text',
            'ImageField': 'text',
            'FileField': 'text',
            'SlugField': 'text',
            'EmailField': 'text',
            'URLField': 'text',
            'AutoField': 'number',
            'BigAutoField': 'number',
        }
        if t == 'CharField' and hasattr(field, 'choices') and field.choices:
            return 'select'
        return mapping.get(t, 'text')

    def GetTables(self):
        # 性能优化：使用 Django cache 缓存 count（TTL 300 秒），避免每次列表请求
        # 都触发 N 次 COUNT 查询。count 失败时返回 -1 但不缓存，下次仍会尝试查询。
        from django.core.cache import cache
        tables = []
        for name, model in self.TABLE_MAP.items():
            cache_key = f'form_service:count:{name}'
            try:
                count = cache.get(cache_key)
                if count is None:
                    count = model.objects.count()
                    cache.set(cache_key, count, 300)
            except (DatabaseError, TypeError, ValueError) as e:
                logger.warning('GetTables: %s count 失败: %s', name, e)
                count = -1
            tables.append({
                'name': name,
                'title': name,
                'count': count,
                'type': 'dynamic',
                'source': 'model',
            })
        return tables

    def GetColumns(self, table_name):
        model = self.TABLE_MAP.get(table_name)
        if not model:
            return []
        try:
            columns = []
            for f in model._meta.get_fields():
                if not hasattr(f, 'name') or f.name in ('id',):
                    continue
                if f.auto_created and not f.concrete:
                    continue
                if f.is_relation and not f.concrete:
                    continue
                is_pk = (f.name == model._meta.pk.name) if model._meta.pk else False
                pascal_name = f.name
                field_type = self._MapFieldType(f)
                col = {
                    'field': pascal_name,
                    'title': pascal_name,
                    'type': field_type,
                    'editable': getattr(f, 'editable', True) and not is_pk,
                    'sortable': True,
                    'filterable': True,
                    'primaryKey': is_pk,
                    'hideInForm': is_pk,
                }
                if field_type == 'select' and hasattr(f, 'choices') and f.choices:
                    col['options'] = [{'value': k, 'label': str(v)} for k, v in f.choices]
                if field_type == 'number':
                    col['min'] = None
                    col['max'] = None
                    for v in getattr(f, 'validators', []):
                        vname = v.__class__.__name__
                        if vname == 'MinValueValidator':
                            col['min'] = v.limit_value
                        elif vname == 'MaxValueValidator':
                            col['max'] = v.limit_value
                    if col['min'] is None and col['max'] is None:
                        col.pop('min', None)
                        col.pop('max', None)
                if field_type == 'text' and hasattr(f, 'max_length') and f.max_length:
                    col['maxLength'] = f.max_length
                if getattr(f, 'null', False):
                    col['required'] = False
                # ForeignKey 字段：检测关联模型，自动使用实体选择器
                if f.__class__.__name__ == 'ForeignKey':
                    related_model = getattr(f, 'related_model', None)
                    if related_model:
                        related_name = related_model.__name__
                        ep_config = self.ENTITY_PICKER_MAP.get(related_name)
                        if ep_config:
                            col['type'] = 'entityPicker'
                            col['entityPicker'] = ep_config
                columns.append(col)
            return columns
        except Exception:
            return []

    def GetTable(self, table_name, params):
        model = self.TABLE_MAP.get(table_name)
        if not model:
            return {'records': [], 'total': 0}
        qs = model.objects.all()
        keyword = params.get('keyword', '')
        if keyword:
            or_conditions = {}
            for f in model._meta.get_fields():
                if hasattr(f, 'name') and f.__class__.__name__ in ('CharField', 'TextField'):
                    or_conditions[f'{f.name}__icontains'] = keyword
            if or_conditions:
                q = Q()
                for k, v in or_conditions.items():
                    q |= Q(**{k: v})
                qs = qs.filter(q)
        try:
            page = int(params.get('PageIndex', 1))
        except (ValueError, TypeError):
            page = 1
        try:
            page_size = min(max(int(params.get('PageSize', 20)), 1), 200)
        except (ValueError, TypeError):
            page_size = 20
        total = qs.count()
        start = (page - 1) * page_size
        records = [_PascalizeRecord(r, model) for r in qs[start:start + page_size].values()]
        # 注意：键名大小写不一致（records/total/page 为 lowercase，PageSize 为 PascalCase）。
        # 调用方 form_api.py 依赖小写键名 result['records']、result['total']，
        # 在未同步修改所有调用方前保持现状；参考 msync/orm_handler.py 已统一为 PascalCase。
        return {'records': records, 'total': total, 'page': page, 'PageSize': page_size}

    def AddRecord(self, data):
        table_name = data.get('Table', '')
        model = self.TABLE_MAP.get(table_name)
        if not model:
            return None
        pascal_map = self._BuildFieldMap(model)
        create_data = {}
        for key, val in data.items():
            if key in ('table', 'action', 'Table', 'Action', 'pk', 'PK', 'uuid', 'UUID'):
                continue
            resolved = self._ResolveFieldName(key, pascal_map)
            if resolved is None:
                continue
            field = pascal_map.get(resolved) or pascal_map.get(key)
            if field is None:
                continue
            if isinstance(val, list) and len(val) == 1:
                val = val[0]
            val = self._ConvertValueForField(field, val)
            is_fk = field.__class__.__name__ == 'ForeignKey'
            attr_name = field.attname if is_fk else resolved
            create_data[attr_name] = val
        pk_field_name = model._meta.pk.name if model._meta.pk else None
        if pk_field_name and pk_field_name not in create_data:
            pk_field = model._meta.pk
            if pk_field.__class__.__name__ in ('UUIDField', 'BinaryField', 'FixedBinaryField'):
                create_data[pk_field_name] = uuid.uuid4().bytes
        obj = model.objects.create(**create_data)
        return {'PK': str(obj.pk) if obj.pk else None}

    def ModifyRecord(self, data):
        table_name = data.get('Table', '')
        model = self.TABLE_MAP.get(table_name)
        if not model:
            return None
        pk = data.get('PK')
        if not pk:
            return None
        pk_field_name = model._meta.pk.name
        pk_field = model._meta.pk
        pk_value = self._ConvertValueForField(pk_field, pk)
        try:
            obj = model.objects.get(**{pk_field_name: pk_value})
        except model.DoesNotExist:
            return None
        pascal_map = self._BuildFieldMap(model)
        update_fields = []
        for key, val in data.items():
            if key in ('table', 'action', 'pk', 'uuid', 'Table', 'Action', 'PK', 'UUID'):
                continue
            resolved = self._ResolveFieldName(key, pascal_map)
            if resolved is None:
                continue
            if resolved == model._meta.pk.name:
                continue
            field = pascal_map.get(resolved) or pascal_map.get(key)
            if field is None:
                continue
            if isinstance(val, list) and len(val) == 1:
                val = val[0]
            val = self._ConvertValueForField(field, val)
            is_fk = field.__class__.__name__ == 'ForeignKey'
            attr_name = field.attname if is_fk else resolved
            setattr(obj, attr_name, val)
            update_fields.append(attr_name)
        if update_fields:
            obj.save(update_fields=update_fields)
        return {'PK': str(obj.pk) if obj.pk else None}

    def ModifyItem(self, data):
        return self.ModifyRecord(data)

    def DeleteRecord(self, data):
        table_name = data.get('Table', '')
        model = self.TABLE_MAP.get(table_name)
        if not model:
            return False
        pk = data.get('PK')
        if not pk:
            return False
        pk_field_name = model._meta.pk.name
        pk_field = model._meta.pk
        pk_value = self._ConvertValueForField(pk_field, pk)
        # 使用 filter().delete() 单次查询完成删除，避免 get().delete() 的 SELECT+DELETE 两次查询。
        # 保持返回布尔值以兼容调用方 form_api.py 的 `if not result:` 判断。
        deleted, _ = model.objects.filter(**{pk_field_name: pk_value}).delete()
        return bool(deleted)

    def CodeDisplay(self, data):
        table_name = data.get('Table', '')
        code_field = data.get('CodeField', 'PermCode')
        name_field = data.get('NameField', 'PermName')
        model = self.TABLE_MAP.get(table_name)
        if not model:
            return []
        fields = {f.name for f in model._meta.get_fields() if hasattr(f, 'name')}
        if code_field not in fields or name_field not in fields:
            return []
        qs = model.objects.all()
        keyword = data.get('Keyword', '')
        if keyword:
            filter_kw = {f'{code_field}__icontains': keyword}
            qs = qs.filter(**filter_kw)
        # 使用 qs.values() 仅查询需要的两列，避免加载完整模型实例再 getattr 取值。
        items = qs.values(code_field, name_field)[:50]
        return [{'code': str(item[code_field]), 'name': str(item.get(name_field, ''))} for item in items]

    def HandleRequest(self, action, data):
        """统一请求入口

        Args:
            action: 操作类型 (tables/columns/list/add/modify/delete/CodeDisplay)
            data: 请求数据

        Returns:
            dict 响应数据

        注：此处使用 if/elif 链而非 dispatch 字典，因各分支含差异化校验逻辑
        （columns/list 需校验表名是否存在，add/modify/delete 需判断结果是否为 None/False）。
        若后续 action 数量继续增长，可考虑拆分为独立 _handle_xxx 方法 + dispatch 字典。
        """
        # 表权限强制校验
        table_name = (data or {}).get('Table', '')
        is_admin = bool(
            getattr(self.user, 'is_staff', False)
            or getattr(self.user, 'is_superuser', False)
        )
        if table_name:
            if table_name in self.ADMIN_ONLY_TABLES and not is_admin:
                return {'code': 1, 'msg': f'表 {table_name} 仅管理员可访问'}
            readonly_actions = {'tables', 'columns', 'list', 'code_display'}
            if (
                table_name in self.READONLY_TABLES_FOR_NORMAL
                and action not in readonly_actions
                and not is_admin
            ):
                return {'code': 1, 'msg': f'表 {table_name} 为只读表，无写权限'}

        if action == 'tables':
            return {'code': 0, 'msg': 'success', 'data': self.GetTables()}
        elif action == 'columns':
            table_name = data.get('Table', '')
            if table_name not in self.TABLE_MAP:
                return {'code': 1, 'msg': f'表 {table_name} 不存在'}
            return {'code': 0, 'msg': 'success', 'data': self.GetColumns(table_name)}
        elif action == 'list':
            table_name = data.get('Table', '')
            if table_name not in self.TABLE_MAP:
                return {'code': 1, 'msg': f'表 {table_name} 不存在'}
            return {'code': 0, 'msg': 'success', 'data': self.GetTable(table_name, data)}
        elif action == 'add':
            result = self.AddRecord(data)
            if result is None:
                return {'code': 1, 'msg': '添加失败'}
            return {'code': 0, 'msg': 'success', 'data': result}
        elif action == 'modify':
            result = self.ModifyRecord(data)
            if result is None:
                return {'code': 1, 'msg': '修改失败'}
            return {'code': 0, 'msg': 'success', 'data': result}
        elif action == 'delete':
            result = self.DeleteRecord(data)
            if not result:
                return {'code': 1, 'msg': '删除失败'}
            return {'code': 0, 'msg': 'success'}
        elif action == 'code_display':
            result = self.CodeDisplay(data)
            return {'code': 0, 'msg': 'success', 'data': result}
        else:
            return {'code': 1, 'msg': f'未知操作: {action}'}

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

    # 子服务器 views.py 中直接实例化 FormService 使用
    # （FormServiceBase 提供 ORM 能力，视图层由子服务器自行封装）
"""

import uuid
import datetime
import logging
from django.db import DatabaseError
from django.db.models import Q
from django.db.models.fields import NOT_PROVIDED

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
        # UUIDObj 处理：gvsdsdk UUIDField 返回 UUIDObj 对象（非 bytes），需转为字符串
        if val is not None and val.__class__.__name__ == 'UUIDObj':
            val = val.str
        elif isinstance(val, bytes) and len(val) == 16:
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
    UUID_FIELD_ENTITY_MAP = {}
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
            # 非空字段：数值类型返回默认值或0，布尔类型返回 False，其他类型返回空字符串
            field_type = field.__class__.__name__
            if field_type in ('SmallIntegerField', 'IntegerField', 'BigIntegerField',
                              'PositiveIntegerField', 'PositiveSmallIntegerField',
                              'FloatField', 'DecimalField'):
                default = getattr(field, 'default', None)
                if default is not None and default is not NOT_PROVIDED:
                    return default
                return 0
            if field_type == 'BooleanField':
                return False
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
                dt = datetime.datetime.fromisoformat(val)
                # USE_TZ=False + MySQL 不接受 aware datetime：
                # 统一转 naive UTC（UTC-0 存储，展示时由前端按用户时区转换）
                if dt.tzinfo is not None:
                    dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
                return dt
            except (ValueError, TypeError):
                return val
        if field_type in ('BooleanField', 'NullBooleanField') and isinstance(val, str):
            low = val.strip().lower()
            if low in ('true', '1', 't', 'yes'):
                return True
            if low in ('false', '0', 'f', 'no'):
                return False
            return val
        max_length = getattr(field, 'max_length', None)
        if max_length and isinstance(val, str) and len(val) > max_length:
            logger.warning('字段 %s 值超长（%d > %d），已截断', getattr(field, 'name', '?'), len(val), max_length)
            val = val[:max_length]
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

    @staticmethod
    def CachedTableCount(Name, Model, TTL=300):
        """带缓存的表行数（供表清单展示，避免每次列表请求触发 N 次 COUNT）

        Args:
            Name: 缓存键使用的表名
            Model: 目标模型
            TTL: 缓存秒数

        Returns:
            int：行数；查询失败返回 -1（不缓存，下次仍会重试）
        """
        from django.core.cache import cache
        cache_key = f'form_service:count:{Name}'
        try:
            count = cache.get(cache_key)
            if count is None:
                count = Model.objects.count()
                cache.set(cache_key, count, TTL)
            return count
        except (DatabaseError, TypeError, ValueError) as e:
            logger.warning('CachedTableCount: %s count 失败: %s', Name, e)
            return -1

    def GetTables(self):
        # 性能优化：表行数走 CachedTableCount（TTL 300 秒），避免每次列表请求
        # 都触发 N 次 COUNT 查询。count 失败时返回 -1 但不缓存，下次仍会尝试查询。
        tables = []
        for name, model in self.TABLE_MAP.items():
            tables.append({
                'name': name,
                'title': name,
                'count': self.CachedTableCount(name, model),
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

    # ── entityPicker 实体标签解析（由 ENTITY_PICKER_MAP / UUID_FIELD_ENTITY_MAP 驱动） ──

    def _GetEntityPickerFields(self, model):
        """获取模型中所有 entityPicker 类型的字段信息

        两类字段都会识别：
        - ForeignKey：按关联模型名查 ENTITY_PICKER_MAP
        - UUIDField（非主键）：按字段名查 UUID_FIELD_ENTITY_MAP，再取 ENTITY_PICKER_MAP 配置

        Returns:
            list[dict]：{'field_name','label_field','value_field','entity_name','extra_fields'}
        """
        result = []
        for f in model._meta.get_fields():
            if not hasattr(f, 'name'):
                continue
            # ForeignKey 字段
            if f.__class__.__name__ == 'ForeignKey':
                try:
                    related_model = getattr(f, 'related_model', None)
                    if related_model:
                        related_name = related_model.__name__
                        ep_config = self.ENTITY_PICKER_MAP.get(related_name)
                        if ep_config:
                            result.append({
                                'field_name': f.name,
                                'label_field': ep_config['labelField'],
                                'value_field': ep_config['valueField'],
                                'entity_name': related_name,
                                'extra_fields': ep_config.get('extraFields', []),
                            })
                except Exception:
                    pass
            # UUIDField 字段（非 ForeignKey）
            elif f.__class__.__name__ == 'UUIDField' and not getattr(f, 'primary_key', False):
                entity_name = self.UUID_FIELD_ENTITY_MAP.get(f.name)
                if entity_name:
                    ep_config = self.ENTITY_PICKER_MAP.get(entity_name)
                    if ep_config:
                        result.append({
                            'field_name': f.name,
                            'label_field': ep_config['labelField'],
                            'value_field': ep_config['valueField'],
                            'entity_name': entity_name,
                            'extra_fields': ep_config.get('extraFields', []),
                        })
        return result

    def _EnrichEntityLabels(self, ObjList, EpFields, Records):
        """为 Records 中的 entityPicker 字段补充 `_label` 后缀字段，以及 extraFields

        支持两种字段类型：
        - ForeignKey：getattr(obj, field_name) 返回关联模型实例，直接取 label_field
        - UUIDField：getattr(obj, field_name) 返回 UUIDObj，需批量查询关联表获取名称

        Args:
            ObjList: 已取出的模型实例列表（调用方通常已 list()，避免重复查库）
            EpFields: _GetEntityPickerFields 的返回值
            Records: 与 ObjList 等长的字典列表，原地补充标签字段
        """
        obj_list = list(ObjList)
        if not obj_list:
            return Records

        # 预构建 UUIDField 类型的 UUID → label 映射（批量查询优化性能）
        # key: (entity_name, label_field), value: {uuid_bytes: label}
        uuid_label_cache = {}
        uuid_extra_cache = {}  # key: (entity_name, extra_field), value: {uuid_bytes: value}

        for ep_info in EpFields:
            field_name = ep_info['field_name']
            entity_name = ep_info['entity_name']
            label_field = ep_info['label_field']
            extra_fields = ep_info.get('extra_fields', [])
            value_field = ep_info['value_field']

            # 判断字段类型
            field_obj = None
            for f in obj_list[0]._meta.get_fields():
                if hasattr(f, 'name') and f.name == field_name:
                    field_obj = f
                    break

            # 仅对 UUIDField（非主键）类型批量查询；ForeignKey 通过 getattr 直接获取
            if not (field_obj and field_obj.__class__.__name__ == 'UUIDField'
                    and not getattr(field_obj, 'primary_key', False)):
                continue

            # 收集所有 UUID 值（转为 bytes）
            uuid_values = set()
            for obj in obj_list:
                val = getattr(obj, field_name, None)
                if val is not None:
                    if hasattr(val, 'bytes'):
                        uuid_values.add(val.bytes)
                    elif isinstance(val, str):
                        try:
                            uuid_values.add(uuid.UUID(val).bytes)
                        except ValueError:
                            pass

            if not uuid_values:
                continue

            related_model = self.TABLE_MAP.get(entity_name)
            if not related_model:
                continue

            # 批量查询关联表
            try:
                related_qs = related_model.objects.filter(**{f'{value_field}__in': uuid_values})
                for r in related_qs:
                    pk_val = getattr(r, value_field, None)
                    pk_bytes = pk_val.bytes if hasattr(pk_val, 'bytes') else pk_val
                    if pk_bytes is None:
                        continue
                    label = getattr(r, label_field, None)
                    if label is not None:
                        uuid_label_cache.setdefault((entity_name, label_field), {})[pk_bytes] = label
                    for ef in extra_fields:
                        ef_val = getattr(r, ef, None)
                        if ef_val is not None:
                            uuid_extra_cache.setdefault((entity_name, ef), {})[pk_bytes] = ef_val
            except Exception:
                pass

        for i, obj in enumerate(obj_list):
            if i >= len(Records):
                break
            for ep_info in EpFields:
                field_name = ep_info['field_name']
                label_field = ep_info['label_field']
                extra_fields = ep_info.get('extra_fields', [])
                entity_name = ep_info['entity_name']

                # 判断字段类型
                field_obj = None
                for f in obj._meta.get_fields():
                    if hasattr(f, 'name') and f.name == field_name:
                        field_obj = f
                        break

                try:
                    # 获取 db_column 作为 key 前缀
                    db_col = None
                    for f in obj._meta.get_fields():
                        if hasattr(f, 'name') and f.name == field_name and hasattr(f, 'db_column'):
                            db_col = f.db_column or f.name
                            break
                    key_prefix = db_col or field_name

                    is_uuid_field = (field_obj
                                     and field_obj.__class__.__name__ == 'UUIDField'
                                     and not getattr(field_obj, 'primary_key', False))

                    if is_uuid_field:
                        # UUIDField 类型：从缓存查找
                        val = getattr(obj, field_name, None)
                        if val is None:
                            continue
                        val_bytes = val.bytes if hasattr(val, 'bytes') else val
                        if val_bytes is None:
                            continue
                        label = uuid_label_cache.get((entity_name, label_field), {}).get(val_bytes)
                        if label is not None:
                            Records[i][key_prefix + '_label'] = str(label)
                        for ef in extra_fields:
                            ef_val = uuid_extra_cache.get((entity_name, ef), {}).get(val_bytes)
                            if ef_val is not None:
                                Records[i][key_prefix + '_' + ef] = ef_val
                    else:
                        # ForeignKey 类型：直接获取关联实例
                        related_obj = getattr(obj, field_name, None)
                        if related_obj is not None:
                            label = getattr(related_obj, label_field, None)
                            if label is not None:
                                Records[i][key_prefix + '_label'] = str(label)
                            for ef in extra_fields:
                                ef_val = getattr(related_obj, ef, None)
                                if ef_val is not None:
                                    Records[i][key_prefix + '_' + ef] = ef_val
                except Exception:
                    pass
        return Records

    # ── 查询链公共辅助（本类 GetTable 与子类自定义列表查询复用，避免重复实现） ──

    @staticmethod
    def ParsePagination(params, DefaultPageSize=20, MaxPageSize=None):
        """解析分页参数，兼容 PageIndex/PageSize 与前端常用的 page/pageSize 两套命名

        Args:
            params: 请求参数字典
            DefaultPageSize: 未传或非法时的页大小
            MaxPageSize: 页大小上限，None 表示不限制

        Returns:
            (page, page_size)：page 从 1 起（非法值回落 1，避免负切片）
        """
        try:
            page = int(params.get('PageIndex') or params.get('page') or 1)
        except (ValueError, TypeError):
            page = 1
        if page < 1:
            page = 1
        try:
            page_size = int(params.get('PageSize') or params.get('pageSize') or DefaultPageSize)
        except (ValueError, TypeError):
            page_size = DefaultPageSize
        if page_size < 1:
            page_size = DefaultPageSize
        if MaxPageSize:
            page_size = min(page_size, MaxPageSize)
        return page, page_size

    @staticmethod
    def ApplyKeyword(qs, model, keyword):
        """关键字模糊过滤：对模型所有 CharField/TextField 做 OR icontains"""
        if not keyword:
            return qs
        or_conditions = {}
        for f in model._meta.get_fields():
            if hasattr(f, 'name') and f.__class__.__name__ in ('CharField', 'TextField'):
                or_conditions[f'{f.name}__icontains'] = keyword
        if not or_conditions:
            return qs
        q = Q()
        for k, v in or_conditions.items():
            q |= Q(**{k: v})
        return qs.filter(q)

    def ApplyExactFilters(self, qs, model, params, SkipKeys=None):
        """把与模型字段同名的请求参数用作精确过滤

        UUIDField/ForeignKey 自动转 BINARY(16)（ForeignKey 用 attname `xxx_id` 传原始 PK）。

        Args:
            qs: 待过滤 QuerySet
            model: 目标模型
            params: 请求参数字典
            SkipKeys: 不参与过滤的键（分页/搜索等控制参数）
        """
        skip = set(SkipKeys or ())
        field_map = self._BuildFieldMap(model)
        for key, value in params.items():
            if key in skip or not value:
                continue
            field = field_map.get(key)
            if field is None:
                continue
            field_type = field.__class__.__name__
            if field_type == 'ForeignKey':
                try:
                    uuid_bytes = uuid.UUID(str(value)).bytes
                except (ValueError, AttributeError):
                    uuid_bytes = value
                qs = qs.filter(**{key + '_id': uuid_bytes})
            elif field_type in ('UUIDField', 'BinaryField', 'FixedBinaryField'):
                try:
                    qs = qs.filter(**{key: uuid.UUID(str(value)).bytes})
                except (ValueError, AttributeError):
                    qs = qs.filter(**{key: value})
            else:
                qs = qs.filter(**{key: value})
        return qs

    def ApplySelectRelated(self, qs, model, FieldNames):
        """对指定字段中属于 ForeignKey 的做 select_related 预加载

        UUIDField 类型字段（非 FK）会被跳过，避免 select_related 触发 FieldError。
        """
        field_map = self._BuildFieldMap(model)
        fk_names = []
        for name in FieldNames or ():
            field = field_map.get(name)
            if field is not None and field.__class__.__name__ == 'ForeignKey':
                fk_names.append(name)
        if fk_names:
            qs = qs.select_related(*fk_names)
        return qs

    @staticmethod
    def BuildRecords(model, ObjList):
        """模型实例列表 → PascalCase 字典列表

        ForeignKey 取 attname（`xxx_id`）拿原始 PK 值，与前端契约（UUID 字符串）一致。
        """
        records = []
        for obj in ObjList:
            row = {}
            for f in model._meta.concrete_fields:
                f_name = f.attname if f.__class__.__name__ == 'ForeignKey' else f.name
                row[f.name] = getattr(obj, f_name, None)
            records.append(_PascalizeRecord(row, model))
        return records

    def GetTable(self, table_name, params):
        model = self.TABLE_MAP.get(table_name)
        if not model:
            return {'records': [], 'total': 0}
        qs = model.objects.all()
        qs = self.ApplyKeyword(qs, model, params.get('keyword', ''))
        page, page_size = self.ParsePagination(params, MaxPageSize=200)
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

    # ---- 低层落库辅助：供子类通用 CRUD 落库点复用（裸 ORM 收敛到 SDK） ----

    def _CreateObject(self, model, create_data):
        """按已解析字段字典创建记录（通用新增落库点）"""
        return model.objects.create(**create_data)

    def _GetObject(self, model, lookup):
        """按查询条件取单条记录（通用查询落库点，DoesNotExist 由调用方处理）"""
        return model.objects.get(**lookup)

    def _SaveObject(self, obj, update_fields=None):
        """保存记录（通用更新落库点）"""
        if update_fields:
            obj.save(update_fields=update_fields)
        else:
            obj.save()
        return obj

    def _DeleteObject(self, obj):
        """删除记录（通用删除落库点）"""
        obj.delete()

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

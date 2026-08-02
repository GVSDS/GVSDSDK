"""MSYNC ORM 中转处理器

子服务器端注册 Socket.IO 事件处理器，接收主服务器发来的 ORM 查询请求，
在本地数据库执行 SQL 并返回结果。

用法（子服务器端）:
    from gvsdsdk.msync.orm_handler import ORMRelayHandler

    handler = ORMRelayHandler()
    handler.Register(sio_client)  # 注册到 MSyncSocketClient 实例
"""

import datetime
import logging
import re
import uuid

from django.db import DatabaseError
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)

# 安全的 SQL 标识符正则：字母/下划线开头，仅含字母/数字/下划线
_SAFE_IDENTIFIER_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


class ORMRelayHandler:
    """ORM 中转处理器——在子服务器端执行本地 SQL 查询

    通过 MSYNC Socket.IO 事件协议，主服务器将 ORM 请求转发给子服务器，
    子服务器在本地数据库执行查询后返回结果。

    安全：内置敏感表黑名单，防止通过 ORM 中转访问密码、密钥等敏感数据。
    """

    # 敏感表黑名单——这些表不允许通过 ORM 中转访问
    _SENSITIVE_TABLES = frozenset({
        'User', 'UserPassword', 'User2FA', 'UserToken',
        'PaymentGatewayConfig', 'PaymentTransaction',
        'CommissionEvent', 'CommissionRecord',
        'ServiceNode', 'ServiceToken',
        'EphemeralToken', 'Session',
    })
    # 预计算小写版本，避免每次调用 _IsTableAllowed 都重建集合
    _SENSITIVE_TABLES_LOWER = frozenset({t.lower() for t in _SENSITIVE_TABLES})

    def Register(self, sio_client):
        """将 ORM 事件处理器注册到 MSyncSocketClient 实例

        Args:
            sio_client: MSyncSocketClient 实例，需要有 _sio 属性
        """
        sio = sio_client._sio

        @sio.on('msync:orm:tables', namespace='/msync')
        def on_orm_tables(data):
            return self.HandleTables(data)

        @sio.on('msync:orm:columns', namespace='/msync')
        def on_orm_columns(data):
            return self.HandleColumns(data)

        @sio.on('msync:orm:list', namespace='/msync')
        def on_orm_list(data):
            return self.HandleList(data)

        @sio.on('msync:orm:add', namespace='/msync')
        def on_orm_add(data):
            return self.HandleAdd(data)

        @sio.on('msync:orm:modify', namespace='/msync')
        def on_orm_modify(data):
            return self.HandleModify(data)

        @sio.on('msync:orm:delete', namespace='/msync')
        def on_orm_delete(data):
            return self.HandleDelete(data)

        logger.info('[ORM] ORM 中转处理器已注册')

    def _IsTableAllowed(self, table_name):
        """检查表是否允许通过 ORM 中转访问（黑名单过滤）"""
        if not table_name:
            return False
        # 大小写不敏感比较
        return table_name.lower() not in self._SENSITIVE_TABLES_LOWER

    def _ValidateIdentifier(self, name):
        """验证 SQL 标识符（表名/列名）只包含安全字符，防止 f-string 拼接注入"""
        if not name or not _SAFE_IDENTIFIER_RE.match(str(name)):
            raise ValueError(f'非法 SQL 标识符: {name!r}')
        return name

    def _GetConnection(self):
        """获取本地数据库连接（Django default）"""
        from django.db import connections
        conn = connections['default']
        conn.ensure_connection()
        return conn

    def _ValidateAndPrepare(self, data):
        """统一的表名校验与连接获取

        Returns:
            (table_name, conn, error_response):
            - 校验或连接失败时，table_name/conn 为 None，error_response 为错误字典
            - 成功时 error_response 为 None
        """
        table_name = (data or {}).get('Table', '')
        if not table_name:
            return None, None, {'code': 1, 'msg': '缺少 Table 参数'}
        try:
            self._ValidateIdentifier(table_name)
        except ValueError as e:
            return None, None, {'code': 1, 'msg': str(e)}
        if not self._IsTableAllowed(table_name):
            logger.warning('[ORM] 拒绝访问敏感表: %s', table_name)
            return None, None, {'code': 1, 'msg': f'拒绝访问该表: {table_name}'}

        try:
            conn = self._GetConnection()
        except (DatabaseError, ImproperlyConfigured) as e:
            return None, None, {'code': 1, 'msg': f'数据库连接失败: {e}'}

        return table_name, conn, None

    # ─── 事件处理 ──────────────────────────────────────────────

    def HandleTables(self, data):
        """列出本地数据库中所有用户表"""
        try:
            conn = self._GetConnection()
        except (DatabaseError, ImproperlyConfigured) as e:
            return {'code': 1, 'msg': f'数据库连接失败: {e}'}

        try:
            with conn.cursor() as cursor:
                tables = self._ListTables(cursor)
                # 过滤掉敏感表，不在列表中暴露
                tables = [t for t in tables if self._IsTableAllowed(t if isinstance(t, str) else t.get('name', ''))]
                return {'code': 0, 'msg': 'success', 'data': tables}
        except (DatabaseError, KeyError, TypeError, ValueError) as e:
            logger.error(f'[ORM] HandleTables 异常: {e}')
            return {'code': 1, 'msg': f'查询失败: {e}'}

    def HandleColumns(self, data):
        """列出指定表的列信息"""
        table_name, conn, error = self._ValidateAndPrepare(data)
        if error:
            return error

        try:
            with conn.cursor() as cursor:
                columns = self._ListColumns(cursor, table_name)
                return {'code': 0, 'msg': 'success', 'data': columns}
        except (DatabaseError, KeyError, TypeError, ValueError) as e:
            logger.error(f'[ORM] HandleColumns 异常: {e}')
            return {'code': 1, 'msg': f'查询失败: {e}'}

    def HandleList(self, data):
        """分页查询表数据"""
        table_name, conn, error = self._ValidateAndPrepare(data)
        if error:
            return error

        try:
            with conn.cursor() as cursor:
                result = self._QueryTable(cursor, table_name, data or {})
                if result.get('error'):
                    return {'code': 1, 'msg': result['error'], 'data': None}
                return {'code': 0, 'msg': 'success', 'data': result}
        except (DatabaseError, KeyError, TypeError, ValueError) as e:
            logger.error(f'[ORM] HandleList 异常: {e}')
            return {'code': 1, 'msg': f'查询失败: {e}'}

    def HandleAdd(self, data):
        """新增记录"""
        record = (data or {}).get('Record', {})
        table_name, conn, error = self._ValidateAndPrepare(data)
        if error:
            return error

        try:
            with conn.cursor() as cursor:
                result = self._AddRecord(cursor, table_name, record)
                return result
        except (DatabaseError, KeyError, TypeError, ValueError) as e:
            logger.error(f'[ORM] HandleAdd 异常: {e}')
            return {'code': 1, 'msg': f'新增失败: {e}'}

    def HandleModify(self, data):
        """修改记录"""
        record = (data or {}).get('Record', {})
        table_name, conn, error = self._ValidateAndPrepare(data)
        if error:
            return error

        try:
            with conn.cursor() as cursor:
                result = self._ModifyRecord(cursor, table_name, record)
                return result
        except (DatabaseError, KeyError, TypeError, ValueError) as e:
            logger.error(f'[ORM] HandleModify 异常: {e}')
            return {'code': 1, 'msg': f'修改失败: {e}'}

    def HandleDelete(self, data):
        """删除记录"""
        record = (data or {}).get('Record', {})
        table_name, conn, error = self._ValidateAndPrepare(data)
        if error:
            return error

        try:
            with conn.cursor() as cursor:
                result = self._DeleteRecord(cursor, table_name, record)
                return result
        except (DatabaseError, KeyError, TypeError, ValueError) as e:
            logger.error(f'[ORM] HandleDelete 异常: {e}')
            return {'code': 1, 'msg': f'删除失败: {e}'}

    # ─── SQL 执行 ──────────────────────────────────────────────

    def _ListTables(self, cursor):
        """列出所有用户表，含 i18n 标题"""
        cursor.execute(
            "SELECT TABLE_NAME, TABLE_ROWS, TABLE_COMMENT FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE' "
            "ORDER BY TABLE_NAME"
        )

        from django.apps import apps
        # 构建表名→中文标题映射
        model_verbose = {}
        for model in apps.get_models():
            meta = model._meta
            # 优先使用 table_title（自定义 Meta 属性），其次 verbose_name
            table_title = getattr(meta, 'table_title', None)
            title = str(table_title) if table_title else (str(meta.verbose_name) if meta.verbose_name else '')
            if title:
                model_verbose[meta.object_name] = title
                model_verbose[meta.db_table] = title

        result = []
        for row in cursor.fetchall():
            table_name, table_rows, table_comment = row
            # 优先级：TABLE_COMMENT > table_title/verbose_name > 表名
            title = table_comment or model_verbose.get(table_name) or table_name
            result.append({'name': table_name, 'title': title, 'count': table_rows or 0})
        return result

    def _ListColumns(self, cursor, table_name):
        """列出指定表的列信息"""
        cursor.execute(
            "SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_KEY, EXTRA, COLUMN_TYPE, COLUMN_COMMENT "
            "FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
            "ORDER BY ORDINAL_POSITION",
            [table_name]
        )

        model_verbose = self._FindModelVerboseNames(table_name)
        logger.debug(f'[ORM] _ListColumns table={table_name} model_verbose={model_verbose}')

        columns = []
        for row in cursor.fetchall():
            col_name, data_type, is_nullable, col_key, extra, col_type, col_comment = row
            is_pk = col_key == 'PRI'
            is_auto = 'auto_increment' in (extra or '').lower()

            if data_type in ('int', 'bigint', 'tinyint', 'smallint', 'mediumint'):
                ftype = 'number'
            elif data_type in ('float', 'double', 'decimal'):
                ftype = 'number'
            elif data_type in ('datetime', 'timestamp'):
                ftype = 'datetime'
            elif data_type in ('date',):
                ftype = 'date'
            elif data_type in ('time',):
                ftype = 'time'
            elif data_type in ('json',):
                ftype = 'textarea'
            elif data_type in ('text', 'mediumtext', 'longtext', 'tinytext'):
                ftype = 'textarea'
            elif data_type in ('binary', 'varbinary', 'blob'):
                ftype = 'uuid'
            else:
                ftype = 'text'

            title = col_comment or model_verbose.get(col_name) or col_name

            columns.append({
                'field': col_name,
                'title': title,
                'type': ftype,
                'editable': not is_pk and not is_auto,
                'sortable': True,
                'filterable': True,
                'primaryKey': is_pk,
                'hideInForm': is_pk,
                'required': is_nullable == 'NO' and not is_auto,
                'dbType': col_type,
            })
        return columns

    def _QueryTable(self, cursor, table_name, params):
        """分页查询表数据"""
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
            [table_name]
        )
        if cursor.fetchone()[0] == 0:
            return {'records': [], 'total': 0, 'error': f'表不存在: {table_name}'}

        col_info = self._GetTableColumns(cursor, table_name)
        pk_cols = {k: v for k, v in col_info.items() if v['is_pk']}

        where_parts = []
        where_values = []
        for pk_name in pk_cols:
            pk_val = params.get(pk_name, '')
            if pk_val:
                where_parts.append(f'`{pk_name}` = %s')
                where_values.append(self._ConvertValueForDB(pk_val, pk_cols[pk_name]))

        keyword = params.get('keyword', '')
        if not where_parts and keyword:
            cursor.execute(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
                "AND DATA_TYPE IN ('varchar', 'char', 'text', 'mediumtext', 'longtext')",
                [table_name]
            )
            text_cols = [row[0] for row in cursor.fetchall()]
            if text_cols:
                # 转义 LIKE 通配符，防止用户输入的 %/_ 被当通配符
                escaped_keyword = keyword.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
                conditions = [f"`{c}` LIKE %s ESCAPE '\\\\'" for c in text_cols]
                or_clause = ' OR '.join(conditions)
                where_parts.append(f'({or_clause})')
                where_values.extend([f'%{escaped_keyword}%'] * len(text_cols))

        where_clause = (' WHERE ' + ' AND '.join(where_parts)) if where_parts else ''

        if where_clause:
            cursor.execute(f'SELECT COUNT(*) FROM `{table_name}`{where_clause}', where_values)
            total = cursor.fetchone()[0]
        else:
            cursor.execute(f'SELECT COUNT(*) FROM `{table_name}`')
            total = cursor.fetchone()[0]

        try:
            page = max(int(params.get('PageIndex', 1)), 1)
        except (ValueError, TypeError):
            page = 1
        try:
            page_size = min(max(int(params.get('PageSize', 20)), 1), 200)
        except (ValueError, TypeError):
            page_size = 20

        offset = (page - 1) * page_size
        if pk_cols:
            order_by = ', '.join(f'`{pk_name}`' for pk_name in pk_cols)
        else:
            order_by = '1'
        cursor.execute(
            f'SELECT * FROM `{table_name}`{where_clause} ORDER BY {order_by} LIMIT %s OFFSET %s',
            where_values + [page_size, offset]
        )
        desc = [d[0] for d in cursor.description]
        records = []
        for row in cursor.fetchall():
            record = {}
            for i, val in enumerate(row):
                val = self._SerializeValue(val)
                record[desc[i]] = val
            records.append(record)

        return {'Records': records, 'Total': total, 'Page': page, 'PageSize': page_size}

    def _AddRecord(self, cursor, table_name, record):
        """新增记录"""
        if not self._ValidateTableName(cursor, table_name):
            return {'code': 1, 'msg': f'表 {table_name} 不存在'}

        col_info = self._GetTableColumns(cursor, table_name)

        fields = []
        values = []
        for key, val in record.items():
            if key not in col_info:
                continue
            if col_info[key]['is_auto']:
                continue
            fields.append(f'`{key}`')
            values.append(self._ConvertValueForDB(val, col_info[key]))

        if not fields:
            return {'code': 1, 'msg': '没有可插入的字段'}

        placeholders = ', '.join(['%s'] * len(fields))
        sql = f'INSERT INTO `{table_name}` ({", ".join(fields)}) VALUES ({placeholders})'
        cursor.execute(sql, values)
        return {'code': 0, 'msg': '新增成功'}

    def _ModifyRecord(self, cursor, table_name, record):
        """修改记录"""
        if not self._ValidateTableName(cursor, table_name):
            return {'code': 1, 'msg': f'表 {table_name} 不存在'}

        col_info = self._GetTableColumns(cursor, table_name)
        pk_cols = {k: v for k, v in col_info.items() if v['is_pk']}

        if not pk_cols:
            return {'code': 1, 'msg': '表没有主键，无法定位记录'}

        set_parts = []
        set_values = []
        for key, val in record.items():
            if key not in col_info or key in pk_cols:
                continue
            set_parts.append(f'`{key}` = %s')
            set_values.append(self._ConvertValueForDB(val, col_info[key]))

        if not set_parts:
            return {'code': 0, 'msg': '没有需要更新的字段'}

        where_parts = []
        where_values = []
        for pk_name in pk_cols:
            if pk_name not in record:
                return {'code': 1, 'msg': f'缺少主键字段: {pk_name}'}
            where_parts.append(f'`{pk_name}` = %s')
            where_values.append(self._ConvertValueForDB(record[pk_name], pk_cols[pk_name]))

        sql = f'UPDATE `{table_name}` SET {", ".join(set_parts)} WHERE {" AND ".join(where_parts)}'
        cursor.execute(sql, set_values + where_values)

        if cursor.rowcount == 0:
            return {'code': 1, 'msg': '记录不存在或未变更'}
        return {'code': 0, 'msg': '修改成功'}

    def _DeleteRecord(self, cursor, table_name, record):
        """删除记录"""
        if not self._ValidateTableName(cursor, table_name):
            return {'code': 1, 'msg': f'表 {table_name} 不存在'}

        col_info = self._GetTableColumns(cursor, table_name)
        pk_cols = {k: v for k, v in col_info.items() if v['is_pk']}

        if not pk_cols:
            return {'code': 1, 'msg': '表没有主键，无法定位记录'}

        where_parts = []
        where_values = []
        for pk_name in pk_cols:
            if pk_name not in record:
                return {'code': 1, 'msg': f'缺少主键字段: {pk_name}'}
            where_parts.append(f'`{pk_name}` = %s')
            where_values.append(self._ConvertValueForDB(record[pk_name], pk_cols[pk_name]))

        sql = f'DELETE FROM `{table_name}` WHERE {" AND ".join(where_parts)}'
        cursor.execute(sql, where_values)

        if cursor.rowcount == 0:
            return {'code': 1, 'msg': '记录不存在'}
        return {'code': 0, 'msg': '删除成功'}

    # ─── 工具方法 ──────────────────────────────────────────────

    def _ValidateTableName(self, cursor, table_name):
        """验证表名存在"""
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
            [table_name]
        )
        return cursor.fetchone()[0] > 0

    def _GetTableColumns(self, cursor, table_name):
        """获取表的列信息"""
        cursor.execute(
            "SELECT COLUMN_NAME, DATA_TYPE, COLUMN_TYPE, COLUMN_KEY, EXTRA, IS_NULLABLE "
            "FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
            "ORDER BY ORDINAL_POSITION",
            [table_name]
        )
        cols = {}
        for row in cursor.fetchall():
            col_name, data_type, column_type, col_key, extra, is_nullable = row
            cols[col_name] = {
                'type': data_type,
                'column_type': column_type or '',
                'is_pk': col_key == 'PRI',
                'is_auto': 'auto_increment' in (extra or '').lower(),
                'is_nullable': is_nullable == 'YES',
            }
        return cols

    def _ConvertValueForDB(self, value, col_info):
        """将前端传来的值转换为数据库可接受的类型"""
        if value is None or value == '':
            if col_info['is_nullable']:
                return None
            # 非空列：按列类型返回类型适当的默认值，避免空串写入非字符串列
            # 导致 MySQL 静默转换（如 '' → 0）或类型不匹配错误
            data_type = col_info['type']
            if data_type in ('int', 'bigint', 'tinyint', 'smallint', 'mediumint',
                             'float', 'double', 'decimal'):
                return 0
            if data_type == 'json':
                return {}
            # 字符串类列返回空串；其他类型（binary/datetime 等）返回 None 触发 DB 默认值或错误
            if data_type in ('varchar', 'char', 'text', 'mediumtext', 'longtext', 'tinytext', 'enum'):
                return ''
            return None

        data_type = col_info['type']

        if data_type in ('int', 'bigint', 'tinyint', 'smallint', 'mediumint'):
            return int(value)
        elif data_type in ('float', 'double', 'decimal'):
            return float(value)
        elif data_type in ('binary', 'varbinary'):
            hex_str = re.sub(r'[^0-9a-fA-F]', '', str(value))
            if len(hex_str) == 32:
                return uuid.UUID(hex_str).bytes
            return value

        return value

    def _SerializeValue(self, val):
        """将数据库值序列化为 JSON 安全格式"""
        if isinstance(val, bytes):
            if len(val) == 16:
                try:
                    return str(uuid.UUID(bytes=val))
                except ValueError:
                    return val.hex()
            return val.hex()
        elif isinstance(val, datetime.datetime):
            return val.strftime('%Y-%m-%d %H:%M:%S')
        elif isinstance(val, datetime.date):
            return val.isoformat()
        elif isinstance(val, datetime.time):
            return val.strftime('%H:%M:%S')
        elif isinstance(val, (bytearray, memoryview)):
            return bytes(val).hex()
        return val

    def _FindModelVerboseNames(self, table_name):
        """从 Django 模型中获取字段的 verbose_name"""
        from django.apps import apps
        verbose_map = {}
        try:
            for model in apps.get_models():
                if model._meta.db_table == table_name:
                    logger.debug(f'[ORM] _FindModelVerboseNames matched model={model.__name__} db_table={model._meta.db_table}')
                    for f in model._meta.get_fields():
                        # 获取数据库列名
                        col_name = None
                        if hasattr(f, 'column') and f.column:
                            col_name = f.column
                        elif hasattr(f, 'db_column') and f.db_column:
                            col_name = f.db_column
                        elif hasattr(f, 'attname'):
                            col_name = f.attname

                        if not col_name:
                            continue

                        vn = getattr(f, 'verbose_name', None)
                        if vn and str(vn) != col_name:
                            verbose_map[col_name] = str(vn)
                            logger.debug(f'[ORM]   {col_name} -> {str(vn)}')
                    break
            else:
                # 没有匹配的模型
                available = [m._meta.db_table for m in apps.get_models()]
                logger.warning(f'[ORM] _FindModelVerboseNames: 未找到 db_table={table_name} 的模型, 可用表: {available[:20]}...')
        except (AttributeError, KeyError, LookupError) as e:
            logger.error(f'[ORM] _FindModelVerboseNames 异常: {e}')
        return verbose_map

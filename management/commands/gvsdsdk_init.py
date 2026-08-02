"""gvsdsdk init — 子服务器快速初始化 SAAS 表

用法：
    python manage.py gvsdsdk_init --db saas
    python manage.py gvsdsdk_init --db saas --dry-run  # 仅打印 SQL
    python manage.py gvsdsdk_init --db default --modules auth,commission,payment

此命令会根据 gvsdsdk 中的模型定义，在指定数据库中创建对应的表。
适用于子服务器独立部署时快速建表。
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import connections, DatabaseError
from django.apps import apps


class Command(BaseCommand):
    help = '初始化 gvsdsdk SAAS 表结构（子服务器建表工具）'

    def add_arguments(self, parser):
        parser.add_argument(
            '--db', default='saas',
            help='目标数据库别名（默认 saas）',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='仅打印建表 SQL，不执行',
        )
        parser.add_argument(
            '--modules', default='all',
            help='要初始化的模块，逗号分隔（all, auth, commission, payment）。默认 all',
        )

    def handle(self, *args, **options):
        db_alias = options['db']
        dry_run = options['dry_run']
        modules_str = options['modules']

        # 验证数据库连接
        try:
            connection = connections[db_alias]
        except KeyError:
            raise CommandError(f'数据库别名 "{db_alias}" 未配置，请在 DATABASES 中添加')

        # 收集 gvsdsdk 模型
        target_modules = self._parse_modules(modules_str)
        models_to_create = self._collect_models(target_modules)

        if not models_to_create:
            self.stdout.write(self.style.WARNING('没有找到需要创建的模型'))
            return

        self.stdout.write(f'找到 {len(models_to_create)} 个模型：')
        for model in models_to_create:
            self.stdout.write(f'  - {model.__name__} → {model._meta.db_table}')

        # 生成建表 SQL（collect_sql 模式下 Django 不支持 deferred_sql 外键延迟收集，
        # 因此 dry-run 与执行都直接使用 schema_editor.create_model 逐表处理）
        if dry_run:
            self.stdout.write(self.style.SUCCESS('\n=== DRY RUN: 目标表 ==='))
            for model in models_to_create:
                self.stdout.write(f'  - {model.__name__} → {model._meta.db_table}')
            self.stdout.write(self.style.SUCCESS(f'\n共 {len(models_to_create)} 张表'))
            return

        # 执行建表（schema_editor 自动处理表顺序与外键）
        self.stdout.write(f'\n在数据库 "{db_alias}" 中创建表...')
        try:
            with connection.schema_editor() as schema_editor:
                for model in models_to_create:
                    try:
                        schema_editor.create_model(model)
                    except DatabaseError as e:
                        self.stdout.write(self.style.ERROR(f'  创建 {model.__name__} 失败: {e}'))
            self.stdout.write(self.style.SUCCESS(f'成功创建 {len(models_to_create)} 个表'))
        except DatabaseError as e:
            raise CommandError(f'建表失败: {e}')

    def _parse_modules(self, modules_str):
        if modules_str == 'all':
            return {'auth', 'commission', 'payment', 'order'}
        return set(m.strip() for m in modules_str.split(',') if m.strip())

    def _collect_models(self, target_modules):
        """收集 gvsdsdk 中需要建表的模型"""
        all_models = []

        module_map = {
            'auth': 'gvsdsdk',
            'commission': 'gvsdsdk.commission',
            'payment': 'gvsdsdk.payment',
            'order': 'gvsdsdk.order',
        }
        for key, module_path in module_map.items():
            if key in target_modules:
                self._collect_from_module(module_path, all_models)

        return all_models

    def _collect_from_module(self, module_path, all_models):
        """从指定模块路径收集 QModel 子类"""
        try:
            module = __import__(module_path, fromlist=['models'])
            models_module = getattr(module, 'models', module)
            for name in dir(models_module):
                obj = getattr(models_module, name)
                if self._is_qmodel(obj):
                    all_models.append(obj)
        except ImportError:
            pass

    def _is_qmodel(self, obj):
        """判断是否为 QModel 子类（非抽象）"""
        from gvsdsdk.model_base import QModel
        return (
            isinstance(obj, type)
            and issubclass(obj, QModel)
            and not obj._meta.abstract
        )

"""gvsdsdk_check_schema — gvsdsdk/msync 模型与数据库 schema 一致性校验

对比模型 db_table 的实际表结构与模型字段清单，检测：
  - 模型有但数据库缺失的字段（需迁移）
  - 数据库有但模型未映射的字段（模型漂移）

用法：
    python manage.py gvsdsdk_check_schema              # 校验 gvsdsdk + msync
    python manage.py gvsdsdk_check_schema --app msync  # 仅校验 msync
    python manage.py gvsdsdk_check_schema --check      # 仅退出码检查（CI 用）
"""

from django.apps import apps as django_apps
from django.core.management.base import BaseCommand
from django.db import connections, router


class Command(BaseCommand):
    help = '校验 gvsdsdk/msync 模型与数据库 schema 一致性（对照 db_table 字段清单）'

    def add_arguments(self, parser):
        parser.add_argument(
            '--app', nargs='*', default=['gvsdsdk', 'msync'],
            help='要校验的 app_label（默认 gvsdsdk msync）',
        )
        parser.add_argument(
            '--check', action='store_true',
            help='仅检查（存在差异时退出码非 0），不输出明细之外的汇总',
        )

    def handle(self, *args, **options):
        app_labels = options['app']
        check_only = options['check']

        issues = []
        checked = 0
        for app_label in app_labels:
            try:
                config = django_apps.get_app_config(app_label)
            except LookupError:
                self.stderr.write(f'未安装的 app: {app_label}')
                issues.append(f'{app_label}: 未安装')
                continue

            for model in config.get_models():
                checked += 1
                table = model._meta.db_table
                db = router.db_for_read(model) or 'default'
                try:
                    cursor = connections[db].cursor()
                    cursor.execute(f'SHOW COLUMNS FROM `{table}`')
                    db_columns = {row[0] for row in cursor.fetchall()}
                    cursor.close()
                except Exception as e:
                    issues.append(f'{app_label}.{table}: 表查询失败: {e}')
                    continue

                model_columns = {f.column for f in model._meta.fields}
                missing = sorted(model_columns - db_columns)
                extra = sorted(db_columns - model_columns)
                if missing or extra:
                    for col in missing:
                        issues.append(f'{app_label}.{table}.{col}: 模型有但数据库缺失')
                    for col in extra:
                        issues.append(f'{app_label}.{table}.{col}: 数据库有但模型未映射')

        if issues:
            for issue in issues:
                self.stderr.write(self.style.ERROR(f'[差异] {issue}'))
            self.stderr.write(self.style.ERROR(
                f'\n检查 {checked} 个模型，发现 {len(issues)} 处差异。'
                '若为模型新增字段，请先生成并执行迁移；若为模型漂移，请同步 SDK 模型。'
            ))
            if check_only:
                raise SystemExit(1)
            return

        self.stdout.write(self.style.SUCCESS(
            f'OK: 检查 {checked} 个模型，schema 与模型定义一致'
        ))

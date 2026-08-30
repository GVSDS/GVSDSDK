"""修正「通用部门」菜单图标

0003 迁移插入菜单时用了占位图标 'icon-menu'，前端 el-icon 无此组件导致图标空白。
改为 Element Plus 有效图标 'OfficeBuilding'（与路由 meta 一致，对齐"通用部门"语义）。
"""
from django.db import migrations

_FIX_ICON = """
UPDATE `Menu`
SET `MenuIcon` = 'OfficeBuilding'
WHERE `MenuLink` = '/hr/position-category' AND `MenuIcon` = 'icon-menu';
"""

_REVERSE = migrations.RunSQL.noop


class Migration(migrations.Migration):

    dependencies = [
        ('gvsdsdk', '0004_fix_position_category_menu'),
    ]

    operations = [
        migrations.RunSQL(_FIX_ICON, _REVERSE),
    ]

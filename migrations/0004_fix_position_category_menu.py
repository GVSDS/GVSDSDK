"""修正「通用部门」菜单挂载层级

0003 迁移把「通用部门」子菜单挂到了「职务管理」页面（position-manage）下，
而前端把带子菜单的项渲染为可展开容器、叶子渲染为页面：
「职务管理」是页面（pages/hr/position.html），挂子菜单会导致它变成容器、无法打开页面。

正确结构（复用既有「职务」容器菜单 hr-position，MenuLink 为空）：
    hr-position（职务，容器）
      ├── position-manage（职务管理，页面）
      └── position-category（通用部门，页面）

本迁移将「通用部门」的 ParentMenuUUID 改为 hr-position 的 MenuUUID（幂等）。
"""
from django.db import migrations

_FIX_MENU_PARENT = """
UPDATE `Menu` m
JOIN `Menu` p ON p.`MenuName` = 'hr-position' AND p.`MenuStatus` = 1
SET m.`ParentMenuUUID` = p.`MenuUUID`
WHERE m.`MenuLink` = '/hr/position-category';
"""

# 反向：无明确旧值可回（幂等修正），置为 noop
_REVERSE = migrations.RunSQL.noop


class Migration(migrations.Migration):

    dependencies = [
        ('gvsdsdk', '0003_position_category'),
    ]

    operations = [
        migrations.RunSQL(_FIX_MENU_PARENT, _REVERSE),
    ]

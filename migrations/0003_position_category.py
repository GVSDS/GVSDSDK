"""新增通用部门表 PositionCategory + Position 绑定通用部门

功能背景：
- 通用部门（职务分类目录）为租户级数据（如 财务=FIN / 行政=ADM / 运维=OPS / 人事=HR），
  每个通用部门有编码缩写，供职务编码自动编号（缩写+序号，如 FIN01）。
- Position 表新增 CategoryUUID（可空，兼容存量），绑定通用部门。

本迁移在 default 库执行：
1. 建表 PositionCategory
2. ALTER TABLE Position ADD COLUMN CategoryUUID BINARY(16) NULL
3. 在"职务管理"菜单下插入"通用部门"子菜单（幂等：MenuLink 已存在则跳过）
"""
from django.db import migrations

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS `PositionCategory` (
  `PositionCategoryUUID` BINARY(16) NOT NULL,
  `TenantUUID` BINARY(16) NOT NULL,
  `CategoryName` VARCHAR(128) NOT NULL,
  `CategoryCode` VARCHAR(64) NOT NULL,
  `CategorySort` SMALLINT NOT NULL DEFAULT 0,
  `CategoryStatus` SMALLINT NOT NULL DEFAULT 1,
  `CategoryDesc` VARCHAR(256) NOT NULL DEFAULT '',
  `CreateTime` DATETIME(6) NOT NULL,
  `UpdateTime` DATETIME(6) NOT NULL,
  PRIMARY KEY (`PositionCategoryUUID`),
  KEY `idx_positioncategory_tenant` (`TenantUUID`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

_DROP_TABLE = """
DROP TABLE IF EXISTS `PositionCategory`;
"""

_ADD_COLUMN = """
ALTER TABLE `Position`
  ADD COLUMN `CategoryUUID` BINARY(16) NULL;
"""

_DROP_COLUMN = """
ALTER TABLE `Position`
  DROP COLUMN `CategoryUUID`;
"""

# 幂等插入"通用部门"子菜单：挂在职务管理菜单下，菜单链接 /hr/position-category
_INSERT_MENU = """
INSERT INTO `Menu`
  (`MenuUUID`, `TenantUUID`, `CompanyUUID`, `ParentMenuUUID`, `MenuName`, `MenuTitle`,
   `MenuIcon`, `MenuLink`, `MenuSort`, `PermissionLevel`, `PermissionAttr`, `MenuStatus`, `MenuType`)
SELECT UNHEX(REPLACE(UUID(), '-', '')), NULL, NULL, m.`MenuUUID`, '通用部门', '通用部门',
       'icon-menu', '/hr/position-category', m.`MenuSort` + 1, 1.0, '', 1, 0
FROM `Menu` m
WHERE m.`MenuLink` IN ('/hr/position', '#/hr/position', 'pages/hr/position.html')
  AND NOT EXISTS (SELECT 1 FROM `Menu` x WHERE x.`MenuLink` = '/hr/position-category');
"""

_REMOVE_MENU = """
DELETE FROM `Menu` WHERE `MenuLink` = '/hr/position-category';
"""


class Migration(migrations.Migration):

    dependencies = [
        ('gvsdsdk', '0002_drop_userrole_legacy_fields'),
    ]

    operations = [
        migrations.RunSQL(_CREATE_TABLE, _DROP_TABLE),
        migrations.RunSQL(_ADD_COLUMN, _DROP_COLUMN),
        migrations.RunSQL(_INSERT_MENU, _REMOVE_MENU),
    ]

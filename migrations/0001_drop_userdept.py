"""移除废弃的 UserDept 表

该表已由 UserRole.CompanyUUID + UserRole.DeptUUID 取代，
所有员工部门关联均走 UserRole，不再需要独立映射表。
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = []

    operations = [
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS `UserDept`;",
            reverse_sql="CREATE TABLE `UserDept` (`UserDeptUUID` BINARY(16) NOT NULL PRIMARY KEY, `UserUUID` BINARY(16) NOT NULL, `DeptUUID` BINARY(16) NOT NULL, `PositionUUID` BINARY(16) NULL, `IsMainDept` SMALLINT NOT NULL DEFAULT 1, `CreateTime` DATETIME NOT NULL);",
        ),
    ]

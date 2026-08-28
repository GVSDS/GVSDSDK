"""移除 UserRole 遗留字段（DeptUUID / WorkgroupUUID / RoleType）

部门/工作组归属与成员类型已迁移到 UserRoleDepartment / UserRoleWorkgroup
关联表（负责人/组长判定以关联表 RoleType=1 为准），UserRole 表自身不再
保存部门/工作组归属，RoleType 一并废弃。

步骤（在 default 库执行）：
1. 回填 UserRole.DeptUUID → UserRoleDepartment（去重，防丢存量）
2. 回填 UserRole.WorkgroupUUID → UserRoleWorkgroup（去重，防丢存量）
3. 删除 UserRole.DeptUUID/WorkgroupUUID 上的外键约束（旧 gerp 0006/0016 迁移创建，
   名称自动生成，需从 information_schema 动态查）
4. DROP COLUMN DeptUUID / WorkgroupUUID / RoleType

执行前请确认已备份（用户已备份）。
"""
from django.db import migrations

# 回填部门归属：UserRole.DeptUUID → UserRoleDepartment
# - 租户 UUID 从关联 Company 推导；RoleType 仅 1（负责人）保留，其余按成员(0)落
# - 同一 (UserRoleUUID, DeptUUID) 已存在则跳过
_BACKFILL_DEPT = """
INSERT INTO `UserRoleDepartment`
  (`UserRoleDepartmentUUID`, `UserRoleUUID`, `DeptUUID`, `TenantUUID`, `CompanyUUID`, `RoleType`, `CreateTime`)
SELECT UNHEX(REPLACE(UUID(), '-', '')), ur.`UserRoleUUID`, ur.`DeptUUID`,
       c.`TenantUUID`, ur.`CompanyUUID`, CASE WHEN ur.`RoleType` = 1 THEN 1 ELSE 0 END, NOW()
FROM `UserRole` ur
LEFT JOIN `Company` c ON c.`CompanyUUID` = ur.`CompanyUUID`
WHERE ur.`DeptUUID` IS NOT NULL
  AND ur.`DeptUUID` <> X'00000000000000000000000000000000'
  AND NOT EXISTS (
    SELECT 1 FROM `UserRoleDepartment` d
    WHERE d.`UserRoleUUID` = ur.`UserRoleUUID` AND d.`DeptUUID` = ur.`DeptUUID`
  );
"""

# 回填工作组归属：UserRole.WorkgroupUUID → UserRoleWorkgroup
_BACKFILL_WORKGROUP = """
INSERT INTO `UserRoleWorkgroup`
  (`UserRoleWorkgroupUUID`, `UserRoleUUID`, `WorkgroupUUID`, `TenantUUID`, `CompanyUUID`, `RoleType`, `CreateTime`)
SELECT UNHEX(REPLACE(UUID(), '-', '')), ur.`UserRoleUUID`, ur.`WorkgroupUUID`,
       c.`TenantUUID`, ur.`CompanyUUID`, CASE WHEN ur.`RoleType` = 1 THEN 1 ELSE 0 END, NOW()
FROM `UserRole` ur
LEFT JOIN `Company` c ON c.`CompanyUUID` = ur.`CompanyUUID`
WHERE ur.`WorkgroupUUID` IS NOT NULL
  AND ur.`WorkgroupUUID` <> X'00000000000000000000000000000000'
  AND NOT EXISTS (
    SELECT 1 FROM `UserRoleWorkgroup` w
    WHERE w.`UserRoleUUID` = ur.`UserRoleUUID` AND w.`WorkgroupUUID` = ur.`WorkgroupUUID`
  );
"""

# 删除遗留列
_DROP_LEGACY = """
ALTER TABLE `UserRole`
  DROP COLUMN `DeptUUID`,
  DROP COLUMN `WorkgroupUUID`,
  DROP COLUMN `RoleType`;
"""

# 反向迁移：仅恢复列结构（回填数据不可恢复）
_RESTORE_LEGACY = """
ALTER TABLE `UserRole`
  ADD COLUMN `DeptUUID` BINARY(16) NULL,
  ADD COLUMN `WorkgroupUUID` BINARY(16) NULL,
  ADD COLUMN `RoleType` SMALLINT NOT NULL DEFAULT 0;
"""


def _drop_legacy_fks(apps, schema_editor):
    """动态删除 UserRole.DeptUUID/WorkgroupUUID 的外键约束

    约束由旧 gerp 迁移（0006 DeptUUID / 0016 WorkgroupUUID）创建，
    约束名由 Django 自动生成（如 UserRole_DeptUUID_44d12079_fk_Dept_DeptUUID），
    无法硬编码，须从 information_schema 查询后逐个 DROP FOREIGN KEY。
    """
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT `CONSTRAINT_NAME` FROM `information_schema`.`KEY_COLUMN_USAGE` "
            "WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'UserRole' "
            "AND `COLUMN_NAME` IN ('DeptUUID', 'WorkgroupUUID') "
            "AND `REFERENCED_TABLE_NAME` IS NOT NULL"
        )
        rows = cursor.fetchall()
        for (fk_name,) in rows:
            cursor.execute("ALTER TABLE `UserRole` DROP FOREIGN KEY `%s`" % fk_name)


class Migration(migrations.Migration):

    dependencies = [
        # 保证 UserRoleDepartment / UserRoleWorkgroup 关联表已由 gerp 迁移创建
        ('gvsdsdk', '0001_drop_userdept'),
        ('gerp', '0032_app_service_online'),
    ]

    operations = [
        migrations.RunSQL(_BACKFILL_DEPT, migrations.RunSQL.noop),
        migrations.RunSQL(_BACKFILL_WORKGROUP, migrations.RunSQL.noop),
        migrations.RunPython(_drop_legacy_fks, migrations.RunPython.noop),
        migrations.RunSQL(_DROP_LEGACY, _RESTORE_LEGACY),
    ]

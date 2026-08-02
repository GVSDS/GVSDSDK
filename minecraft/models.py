"""模块：gvsdsdk.minecraft.models — MC 插件共享表 ORM 映射

当前仅包含 ``EasyAuth`` 表（EasyAuth 插件认证表）。

设计要点：
    - ``app_label='mc'``：模型归属 gvsds.apps.mc app，由 gvsds 主服务器的
      ``gvsds/db_router.py`` 路由到 minecraft 库
    - ``managed=False``：表由 MC 插件管理，Django 不参与迁移
    - ``UserRoleUUID`` 列由本系统 ALTER 添加（BINARY(16) 可空），
      存绑定后创建的 ``UserRole`` 关联 UUID
"""

from gvsdsdk.model_base import QModel
from gvsdsdk.models import UUIDField
from django.db import models


class EasyAuth(QModel):
    """EasyAuth 认证表——MC EasyAuth 插件的玩家凭据

    映射真实表 ``easyauth``（managed=False）。``data`` 列为 JSON，
    其中 ``password`` 字段为 bcrypt hash（``$2a$12$...``），
    验证时使用 ``bcrypt.checkpw``。

    ``UserRoleUUID`` 列由本系统 ALTER 添加（BINARY(16) 可空），
    存绑定后创建的 ``UserRole`` 关联 UUID，用于 MC 插件侧反查 GVSDS 身份。
    """

    AuthID = models.AutoField(primary_key=True, db_column='id', verbose_name='认证ID')
    Username = models.CharField(max_length=255, unique=True, db_column='username', verbose_name='玩家名')
    UsernameLower = models.CharField(max_length=255, db_column='username_lower', verbose_name='玩家名小写')
    PlayerUUID = models.CharField(max_length=255, null=True, blank=True, db_column='uuid', verbose_name='MC玩家UUID字符串')
    Data = models.JSONField(db_column='data', verbose_name='插件数据')
    UserRoleUUID = UUIDField(null=True, blank=True, db_column='UserRoleUUID', verbose_name='绑定用户角色关联UUID')

    class Meta:
        db_table = 'easyauth'
        app_label = 'mc'
        managed = False
        verbose_name = 'EasyAuth认证'
        verbose_name_plural = 'EasyAuth认证'

    def __str__(self):
        return self.Username

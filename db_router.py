"""gvsdsdk.db_router — 开箱即用的 SAAS 数据库路由器

子服务器只需在 settings.py 中配置：
    DATABASE_ROUTERS = ['gvsdsdk.db_router.SAASDatabaseRouter']

即可自动将 gvsdsdk 的所有模型路由到 SAAS 主库。

配置项（settings.py）：
    GVSDSDK_SAAS_DB_ALIAS = 'saas'       # SAAS 数据库别名，默认 'saas'
    GVSDSDK_SAAS_READ_ONLY = True         # 子服务器是否只读，默认 True
"""

import logging

logger = logging.getLogger(__name__)


class SAASDatabaseRouter:
    """SAAS 模型数据库路由器——将 gvsdsdk 模型路由到 SAAS 主库

    用法：
        # settings.py
        DATABASES = {
            'default': { ... },           # 本地数据库
            'saas': {                     # SAAS 主库
                'ENGINE': 'django.db.backends.mysql',
                'NAME': 'Users',
                ...
            },
        }
        DATABASE_ROUTERS = ['gvsdsdk.db_router.SAASDatabaseRouter']

        # 可选配置
        GVSDSDK_SAAS_DB_ALIAS = 'saas'    # 默认 'saas'
        GVSDSDK_SAAS_READ_ONLY = True     # 默认 True（子服务器只读）
    """

    # gvsdsdk 内所有模块前缀（startswith 判定覆盖全部子模块，
    # 无需维护显式模块名清单，避免新增模块时遗漏）
    _GVSDSDK_APP_LABELS = frozenset({
        'gvsdsdk',
    })

    def _get_saas_db(self):
        from django.conf import settings
        return getattr(settings, 'GVSDSDK_SAAS_DB_ALIAS', 'saas')

    def _is_read_only(self):
        from django.conf import settings
        return getattr(settings, 'GVSDSDK_SAAS_READ_ONLY', True)

    def _is_gvsdsdk_model(self, model):
        """判断模型是否属于 gvsdsdk"""
        # 按模块路径判断（gvsdsdk.* 前缀全覆盖）
        module = getattr(model, '__module__', '')
        if module.startswith('gvsdsdk.'):
            return True
        # 按 app_label 判断
        app_label = getattr(model._meta, 'app_label', '')
        if app_label in self._GVSDSDK_APP_LABELS:
            return True
        return False

    def db_for_read(self, model, **hints):
        if self._is_gvsdsdk_model(model):
            return self._get_saas_db()
        return None

    def db_for_write(self, model, **hints):
        if self._is_gvsdsdk_model(model):
            if self._is_read_only():
                # 只读模式下必须拒绝写入，返回 None 会让 Django 路由到 default 库导致数据分裂
                raise RuntimeError(
                    "gvsdsdk SAAS database is read-only for sub-servers. "
                    "Set GVSDSDK_SAAS_READ_ONLY=False to allow writes."
                )
            return self._get_saas_db()
        return None

    def allow_relation(self, obj1, obj2, **hints):
        # gvsdsdk 内部模型关联总是允许
        if obj1._meta.app_label == 'gvsdsdk' and obj2._meta.app_label == 'gvsdsdk':
            return True
        # 非 gvsdsdk 模型交由其他路由器决定（返回 None 而非 False，避免阻断跨库关联）
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        # gvsdsdk 模型均为 managed=False，不参与任何库的迁移
        if app_label in self._GVSDSDK_APP_LABELS:
            return False
        return None

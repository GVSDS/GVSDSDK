from django.apps import AppConfig


class MsyncConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'gvsdsdk.msync'
    verbose_name = '微服务通信'
    verbose_name_plural = '微服务通信'

    def ready(self):
        # 仅在主进程启动时确保 gvsds-master 节点存在
        import os
        if os.environ.get('RUN_MAIN') == 'true' or not os.environ.get('RUN_MAIN'):
            try:
                self._ensure_master_node()
            except Exception:
                pass  # 数据库可能尚未迁移

    def _ensure_master_node(self):
        from gvsdsdk.msync.models import ServiceNode, _utcnow
        import uuid

        service_domain = self._resolve_service_domain()
        master_uuid = uuid.UUID('00000000-0000-0000-0000-000000000001')

        try:
            node = ServiceNode.objects.get(ServiceName='gvsds-master')
            # 更新心跳、状态和元数据
            node.ServiceStatus = 1
            node.ServiceScope = ['*']
            node.ServiceDomain = service_domain
            node.ServiceType = 1
            node.ServiceMetadata = {'DBName': 'Users'}
            node.LastHeartbeat = _utcnow()
            node.save(update_fields=['ServiceStatus', 'ServiceScope', 'ServiceDomain', 'ServiceType', 'ServiceMetadata', 'LastHeartbeat'])
        except ServiceNode.DoesNotExist:
            import bcrypt
            secret = 'gvsds-master-internal'
            secret_hash = bcrypt.hashpw(secret.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            node = ServiceNode(
                ServiceUUID=master_uuid.bytes,
                ServiceName='gvsds-master',
                ServiceDomain=service_domain,
                ServiceSecretHash=secret_hash,
                ServiceScope=['*'],
                ServiceStatus=1,
                ServiceType=1,
                ServiceMetadata={'DBName': 'Users'},
                LastHeartbeat=_utcnow(),
            )
            node.SetSecret(secret)
            node.save()

    @staticmethod
    def _resolve_service_domain():
        """解析主服务器公开域名：环境变量 → settings → 默认值（不依赖 gvsds）"""
        import os
        domain = os.environ.get('MSYNC_SERVICE_DOMAIN') or os.environ.get('APP_MSYNC_SERVICE_DOMAIN')
        if domain:
            return domain
        try:
            from django.conf import settings as _settings
            domain = getattr(_settings, 'GVSDSDK_MSYNC_MASTER_URL', None)
            if domain:
                return str(domain).rstrip('/')
        except Exception:
            pass
        return 'https://www.gvsds.com'

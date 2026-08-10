"""模块：服务注册与发现
提供服务节点的注册、注销、发现、心跳等操作。"""

import uuid
import datetime
import logging
from gvsdsdk.msync.models import ServiceNode, _utcnow

logger = logging.getLogger(__name__)


class RegistryError(Exception):
    pass


class ServiceRegistry:
    """服务注册中心——管理微服务的注册、发现与生命周期"""

    SERVICE_TYPE_WORKER = 0
    SERVICE_TYPE_MASTER = 1

    DEFAULT_SCOPE = ['ogm:read', 'ogm:write', 'registry:read']

    @staticmethod
    def Register(name, domain, secret_hash, raw_secret=None, scope=None, service_type=0, metadata=None):
        """注册新的服务节点（已存在则验证密钥哈希并更新域信息）

        Args:
            name: 服务名称（唯一标识）
            domain: 服务域名或 URL
            secret_hash: 服务密钥的 SHA256 哈希（客户端计算，不再传输明文）
            raw_secret: 服务原始密钥（用于加密存储，认证时需要）
            scope: 权限范围列表
            service_type: 服务类型（0=工作节点, 1=主节点）
            metadata: 服务元数据

        Returns:
            ServiceNode 实例

        Raises:
            RegistryError: 注册失败
        """
        import hashlib
        # 密钥强制：raw_secret 与 secret_hash 必须自洽；
        # 新服务节点必须携带明文密钥，否则后续 HMAC 认证无法完成
        if raw_secret:
            computed_hash = hashlib.sha256(raw_secret.encode('utf-8')).hexdigest()
            if secret_hash and computed_hash != secret_hash:
                raise RegistryError('服务密钥哈希不一致')
            secret_hash = secret_hash or computed_hash
        elif not secret_hash:
            raise RegistryError('服务密钥缺失')

        existing = ServiceNode.objects.filter(ServiceName=name).first()
        if existing:
            if not existing.ServiceSecretHash or existing.ServiceSecretHash != secret_hash:
                raise RegistryError('服务密钥验证失败')
            existing.ServiceDomain = domain
            existing.ServiceScope = scope or ServiceRegistry.DEFAULT_SCOPE
            existing.ServiceStatus = 1
            existing.ServiceType = service_type
            existing.ServiceMetadata = metadata or {}
            if raw_secret:
                existing.SetSecret(raw_secret)
            existing.save()
            logger.info(f'服务重新注册: {name} ({domain})')
            return existing

        if not raw_secret:
            raise RegistryError('新服务注册必须提供 ServiceSecret 密钥')

        node = ServiceNode(
            ServiceUUID=uuid.uuid4().bytes,
            ServiceName=name,
            ServiceDomain=domain,
            ServiceScope=scope or ServiceRegistry.DEFAULT_SCOPE,
            ServiceStatus=1,
            ServiceType=service_type,
            ServiceMetadata=metadata or {},
            ServiceSecretHash=secret_hash,
        )
        if raw_secret:
            node.SetSecret(raw_secret)
        node.save()
        logger.info(f'服务注册成功: {name} ({domain})')
        return node

    @staticmethod
    def Deregister(name):
        """注销服务节点（软删除，设置状态为禁用）

        Args:
            name: 服务名称

        Returns:
            True 如果成功

        Raises:
            RegistryError: 服务不存在
        """
        try:
            node = ServiceNode.objects.get(ServiceName=name)
        except ServiceNode.DoesNotExist:
            raise RegistryError(f'服务不存在: {name}')

        node.ServiceStatus = 0
        node.save(update_fields=['ServiceStatus'])
        logger.info(f'服务已注销: {name}')
        return True

    @staticmethod
    def Discover(name):
        """发现指定服务

        Args:
            name: 服务名称

        Returns:
            服务信息字典或 None
        """
        try:
            node = ServiceNode.objects.get(ServiceName=name, ServiceStatus=1)
        except ServiceNode.DoesNotExist:
            return None

        return {
            'ServiceName': node.ServiceName,
            'ServiceDomain': node.ServiceDomain,
            'ServiceScope': node.ServiceScope,
            'ServiceType': node.ServiceType,
            'ServiceMetadata': node.ServiceMetadata,
            'ServiceStatus': node.ServiceStatus,
            'LastHeartbeat': node.LastHeartbeat.isoformat() if node.LastHeartbeat else None,
        }

    @staticmethod
    def GetNode(name):
        """获取服务节点实例（不按状态过滤，已注销也可查询）；不存在返回 None"""
        return ServiceNode.objects.filter(ServiceName=name).first()

    @staticmethod
    def DiscoverAll(active_only=True, page=1, page_size=50):
        """发现所有已注册服务（分页）

        Args:
            active_only: 是否只返回活跃服务
            page: 页码（从1开始）
            page_size: 每页数量

        Returns:
            包含分页元数据和服务列表的字典
        """
        qs = ServiceNode.objects.all()
        if active_only:
            qs = qs.filter(ServiceStatus=1)

        total = qs.count()
        offset = (page - 1) * page_size
        page_qs = qs[offset:offset + page_size]

        services = [
            {
                'ServiceName': node.ServiceName,
                'ServiceDomain': node.ServiceDomain,
                'ServiceScope': node.ServiceScope,
                'ServiceType': node.ServiceType,
                'ServiceMetadata': node.ServiceMetadata,
                'ServiceStatus': node.ServiceStatus,
                'LastHeartbeat': node.LastHeartbeat.isoformat() if node.LastHeartbeat else None,
            }
            for node in page_qs
        ]

        return {
            'total': total,
            'page': page,
            'page_size': page_size,
            'services': services,
        }

    @staticmethod
    def Heartbeat(name):
        """服务心跳——更新最后心跳时间

        Args:
            name: 服务名称

        Returns:
            True 如果成功

        Raises:
            RegistryError: 服务不存在
        """
        try:
            node = ServiceNode.objects.get(ServiceName=name, ServiceStatus=1)
        except ServiceNode.DoesNotExist:
            raise RegistryError(f'服务不存在或已禁用: {name}')

        node.LastHeartbeat = _utcnow()
        node.save(update_fields=['LastHeartbeat'])
        return True

    @staticmethod
    def UpdateScope(name, scope):
        """更新服务权限范围

        Args:
            name: 服务名称
            scope: 新的权限范围列表

        Raises:
            RegistryError: 服务不存在
        """
        try:
            node = ServiceNode.objects.get(ServiceName=name, ServiceStatus=1)
        except ServiceNode.DoesNotExist:
            raise RegistryError(f'服务不存在或已禁用: {name}')

        node.ServiceScope = scope
        node.save(update_fields=['ServiceScope'])
        logger.info(f'服务权限更新: {name} -> {scope}')
        return True

    @staticmethod
    def RotateSecret(name, new_secret):
        """轮换服务密钥

        Args:
            name: 服务名称
            new_secret: 新密钥（明文）

        Raises:
            RegistryError: 服务不存在
        """
        try:
            node = ServiceNode.objects.get(ServiceName=name, ServiceStatus=1)
        except ServiceNode.DoesNotExist:
            raise RegistryError(f'服务不存在或已禁用: {name}')

        node.SetSecret(new_secret)
        node.save(update_fields=['ServiceSecretHash', 'ServiceSecretEncrypted'])

        from gvsdsdk.msync.models import ServiceToken
        ServiceToken.objects.filter(ServiceUUID=node, TokenStatus=1).update(TokenStatus=0)

        logger.info(f'服务密钥已轮换: {name}')
        return True

    @staticmethod
    def GetInactiveServices(timeout_minutes=5):
        """获取超时未心跳的服务

        Args:
            timeout_minutes: 超时分钟数

        Returns:
            ServiceNode QuerySet
        """
        threshold = _utcnow() - datetime.timedelta(minutes=timeout_minutes)
        return ServiceNode.objects.filter(
            ServiceStatus=1,
            LastHeartbeat__lt=threshold,
        )

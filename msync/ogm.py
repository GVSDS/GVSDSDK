"""模块：对象全局映射（OGM）管理
提供跨服务对象引用的注册、解析、更新、注销操作。
OGM 是微服务间共享对象身份的核心机制：
  - 每个本地对象注册后获得全局唯一 ID
  - 其他服务通过全局 ID 解析到对象归属与本地 ID
  - 支持按类型、按服务查询对象映射"""

import uuid
import datetime
import logging
from gvsdsdk.msync.models import ObjectGlobalMapping, ServiceNode, _utcnow

logger = logging.getLogger(__name__)


class OGMError(Exception):
    pass


class OGMManager:
    """OGM 管理器——对象全局映射的增删改查"""

    @staticmethod
    def Register(object_type, local_id, service_name, global_id=None, metadata=None):
        """注册对象全局映射

        Args:
            object_type: 对象类型（如 'user', 'company', 'asset'）
            local_id: 对象在所属服务中的本地 ID
            service_name: 所属服务名称
            global_id: 自定义全局 ID（不指定则自动生成）
            metadata: 对象元数据

        Returns:
            ObjectGlobalMapping 实例

        Raises:
            OGMError: 注册失败
        """
        try:
            service = ServiceNode.objects.get(ServiceName=service_name, ServiceStatus=1)
        except ServiceNode.DoesNotExist:
            raise OGMError(f'服务不存在或已禁用: {service_name}')

        if ObjectGlobalMapping.objects.filter(
            ObjectType=object_type,
            ObjectLocalID=local_id,
            OwnerServiceUUID=service,
        ).exists():
            raise OGMError(f'对象映射已存在: {object_type}:{local_id}@{service_name}')

        if global_id is None:
            global_id = f'{object_type}:{uuid.uuid4().hex[:16]}'

        if ObjectGlobalMapping.objects.filter(ObjectGlobalID=global_id).exists():
            raise OGMError(f'全局 ID 已被占用: {global_id}')

        ogm = ObjectGlobalMapping(
            OGMUUID=uuid.uuid4().bytes,
            ObjectType=object_type,
            ObjectLocalID=str(local_id),
            ObjectGlobalID=global_id,
            OwnerServiceUUID=service,
            ObjectMetadata=metadata or {},
            ObjectStatus=1,
        )
        ogm.save()
        logger.info(f'OGM 注册: {global_id} -> {object_type}:{local_id}@{service_name}')
        return ogm

    @staticmethod
    def RegisterBatch(items, service_name):
        """批量注册对象全局映射

        Args:
            items: 列表，每项为 dict，含 ObjectType, LocalID, GlobalID(可选), metadata(可选)
            service_name: 所属服务名称

        Returns:
            注册成功的 ObjectGlobalMapping 列表
        """
        try:
            service = ServiceNode.objects.get(ServiceName=service_name, ServiceStatus=1)
        except ServiceNode.DoesNotExist:
            raise OGMError(f'服务不存在或已禁用: {service_name}')

        results = []
        for item in items:
            object_type = item['ObjectType']
            local_id = str(item['ObjectLocalID'])
            global_id = item.get('ObjectGlobalID') or f'{object_type}:{uuid.uuid4().hex[:16]}'
            metadata = item.get('Metadata', {})

            if ObjectGlobalMapping.objects.filter(
                ObjectType=object_type,
                ObjectLocalID=local_id,
                OwnerServiceUUID=service,
            ).exists():
                logger.warning(f'OGM 跳过（已存在）: {object_type}:{local_id}@{service_name}')
                continue

            ogm = ObjectGlobalMapping(
                OGMUUID=uuid.uuid4().bytes,
                ObjectType=object_type,
                ObjectLocalID=local_id,
                ObjectGlobalID=global_id,
                OwnerServiceUUID=service,
                ObjectMetadata=metadata,
                ObjectStatus=1,
            )
            ogm.save()
            results.append(ogm)

        logger.info(f'OGM 批量注册: {len(results)}/{len(items)} 成功')
        return results

    @staticmethod
    def Resolve(global_id):
        """通过全局 ID 解析对象映射

        Args:
            global_id: 全局 ID

        Returns:
            对象信息字典或 None
        """
        try:
            ogm = ObjectGlobalMapping.objects.select_related('OwnerServiceUUID').get(
                ObjectGlobalID=global_id,
                ObjectStatus=1,
            )
        except ObjectGlobalMapping.DoesNotExist:
            return None

        return OGMManager._to_dict(ogm)

    @staticmethod
    def ResolveLocal(object_type, local_id, service_name):
        """通过本地信息解析对象映射

        Args:
            object_type: 对象类型
            local_id: 本地 ID
            service_name: 服务名称

        Returns:
            对象信息字典或 None
        """
        try:
            service = ServiceNode.objects.get(ServiceName=service_name, ServiceStatus=1)
        except ServiceNode.DoesNotExist:
            return None

        try:
            ogm = ObjectGlobalMapping.objects.select_related('OwnerServiceUUID').get(
                ObjectType=object_type,
                ObjectLocalID=str(local_id),
                OwnerServiceUUID=service,
                ObjectStatus=1,
            )
        except ObjectGlobalMapping.DoesNotExist:
            return None

        return OGMManager._to_dict(ogm)

    @staticmethod
    def QueryByType(object_type, active_only=True):
        """按对象类型查询映射

        Args:
            object_type: 对象类型
            active_only: 是否只返回活跃对象

        Returns:
            对象信息字典列表
        """
        qs = ObjectGlobalMapping.objects.select_related('OwnerServiceUUID').filter(ObjectType=object_type)
        if active_only:
            qs = qs.filter(ObjectStatus=1)
        return [OGMManager._to_dict(ogm) for ogm in qs]

    @staticmethod
    def QueryByService(service_name, object_type=None, active_only=True):
        """按服务查询映射

        Args:
            service_name: 服务名称
            object_type: 可选的对象类型过滤
            active_only: 是否只返回活跃对象

        Returns:
            对象信息字典列表
        """
        try:
            service = ServiceNode.objects.get(ServiceName=service_name, ServiceStatus=1)
        except ServiceNode.DoesNotExist:
            return []

        qs = ObjectGlobalMapping.objects.select_related('OwnerServiceUUID').filter(OwnerServiceUUID=service)
        if object_type:
            qs = qs.filter(ObjectType=object_type)
        if active_only:
            qs = qs.filter(ObjectStatus=1)
        return [OGMManager._to_dict(ogm) for ogm in qs]

    @staticmethod
    def Update(global_id, metadata=None):
        """更新对象映射的元数据

        Args:
            global_id: 全局 ID
            metadata: 新的元数据（合并更新）

        Returns:
            更新后的 ObjectGlobalMapping 实例或 None
        """
        try:
            ogm = ObjectGlobalMapping.objects.get(ObjectGlobalID=global_id, ObjectStatus=1)
        except ObjectGlobalMapping.DoesNotExist:
            return None

        if metadata:
            existing = ogm.ObjectMetadata or {}
            existing.update(metadata)
            ogm.ObjectMetadata = existing

        ogm.UpdateTime = _utcnow()
        ogm.save()
        return ogm

    @staticmethod
    def Deregister(global_id):
        """注销对象全局映射（软删除）

        Args:
            global_id: 全局 ID

        Returns:
            True 如果成功
        """
        try:
            ogm = ObjectGlobalMapping.objects.get(ObjectGlobalID=global_id, ObjectStatus=1)
        except ObjectGlobalMapping.DoesNotExist:
            return False

        ogm.ObjectStatus = 0
        ogm.UpdateTime = _utcnow()
        ogm.save(update_fields=['ObjectStatus', 'UpdateTime'])
        logger.info(f'OGM 注销: {global_id}')
        return True

    @staticmethod
    def _to_dict(ogm):
        """将 OGM 实例转换为信息字典"""
        service = ogm.OwnerServiceUUID
        return {
            'OGMUUID': str(ogm.OGMUUID) if isinstance(ogm.OGMUUID, bytes) else ogm.OGMUUID,
            'ObjectType': ogm.ObjectType,
            'ObjectLocalID': ogm.ObjectLocalID,
            'ObjectGlobalID': ogm.ObjectGlobalID,
            'OwnerService': service.ServiceName if service else None,
            'OwnerDomain': service.ServiceDomain if service else None,
            'Metadata': ogm.ObjectMetadata,
            'Status': ogm.ObjectStatus,
            'CreateTime': ogm.CreateTime.isoformat() if ogm.CreateTime else None,
            'UpdateTime': ogm.UpdateTime.isoformat() if ogm.UpdateTime else None,
        }

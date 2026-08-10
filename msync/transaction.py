"""模块：远程事务管理器
基于 Saga 模式的跨服务事务编排引擎，运行于主服务器（账本服务器）。
通过调用各工作服务的本地事务管理器端点来执行与补偿步骤。

架构：
  主服务器 (TransactionManager)
    │
    ├──▶ POST {service_domain}/api/msync/local/execute/
    │    {StepName, payload}
    │    → 工作服务的 LocalTransactionManager.execute_step()
    │
    └──▶ POST {service_domain}/api/msync/local/compensate/
         {StepName, payload, ActionResult}
         → 工作服务的 LocalTransactionManager.compensate_step()

事务生命周期：
  Pending → Running → Committed
                    ↘ Failed → Compensating → Compensated
                    ↘ TimedOut → Compensating → Compensated"""

import uuid
import json
import logging
import os
import datetime
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from django.db import transaction as db_transaction
from gvsdsdk.msync.models import (
    DistributedTransaction,
    TransactionStep,
    ServiceNode,
    _utcnow,
)

logger = logging.getLogger(__name__)

LOCAL_EXECUTE_PATH = '/api/msync/local/execute/'
LOCAL_COMPENSATE_PATH = '/api/msync/local/compensate/'

# ─── 模块级 HTTP 会话（连接池 + 重试 + TLS 验证）─────────────────
_http_session = requests.Session()
_http_session.verify = True  # 显式启用 TLS 证书验证

# 支持通过环境变量指定 CA 证书包（开发/部署灵活性）
_ca_bundle = os.environ.get('REQUESTS_CA_BUNDLE') or os.environ.get('CURL_CA_BUNDLE')
if _ca_bundle:
    _http_session.verify = _ca_bundle

# 配置连接池重试策略
_retry_adapter = HTTPAdapter(
    max_retries=Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[502, 503, 504],
        allowed_methods=['POST'],
    ),
    pool_connections=10,
    pool_maxsize=10,
)
_http_session.mount('https://', _retry_adapter)
_http_session.mount('http://', _retry_adapter)


class TransactionError(Exception):
    pass


class StepExecutionError(Exception):
    def __init__(self, message, step_index=None, status_code=None, response_body=None):
        super().__init__(message)
        self.StepIndex = step_index
        self.StatusCode = status_code
        self.ResponseBody = response_body


class TransactionManager:
    """远程事务管理器——Saga 编排引擎（运行于主服务器）

    调用各工作服务的本地事务管理器来执行步骤：
      - 执行步骤 → POST {service_domain}/api/msync/local/execute/
      - 补偿步骤 → POST {service_domain}/api/msync/local/compensate/
    """

    @staticmethod
    @db_transaction.atomic
    def Create(name, steps_data, description='', transaction_type='saga',
               initiator_service_name=None, reference_ogm_uuid=None,
               payload=None, timeout_seconds=300):
        """创建分布式事务

        Args:
            name: 事务名称
            steps_data: 步骤数据列表，每项为 dict:
                {
                    'name': '步骤名称（对应本地事务管理器注册的处理器名）',
                    'description': '步骤描述',
                    'ParticipantService': '服务名称（账本中注册的服务名）',
                    'ActionPayload': {...},
                    'CompensatePayload': {...},
                    'MaxRetries': 3,
                }
            description: 事务描述
            transaction_type: 事务类型 (saga/2pc)
            initiator_service_name: 发起服务名称
            reference_ogm_uuid: 关联 OGM UUID
            payload: 事务级载荷
            timeout_seconds: 超时秒数

        Returns:
            DistributedTransaction 实例
        """
        if not steps_data:
            raise TransactionError('事务至少需要一个步骤')

        initiator = None
        if initiator_service_name:
            try:
                initiator = ServiceNode.objects.get(ServiceName=initiator_service_name, ServiceStatus=1)
            except ServiceNode.DoesNotExist:
                raise TransactionError(f'发起服务不存在: {initiator_service_name}')

        tx_code = f'DTX-{uuid.uuid4().hex[:12].upper()}'

        dtx = DistributedTransaction(
            TransactionUUID=uuid.uuid4().bytes,
            TransactionCode=tx_code,
            TransactionType=transaction_type,
            TransactionName=name,
            TransactionDescription=description,
            TransactionStatus=DistributedTransaction.TX_STATUS_PENDING,
            InitiatorServiceUUID=initiator,
            ReferenceOGMUUID=reference_ogm_uuid,
            TransactionPayload=payload or {},
            TimeoutSeconds=timeout_seconds,
        )
        dtx.save()

        for idx, step_data in enumerate(steps_data):
            participant = None
            participant_name = step_data.get('ParticipantService')
            if participant_name:
                try:
                    participant = ServiceNode.objects.get(ServiceName=participant_name, ServiceStatus=1)
                except ServiceNode.DoesNotExist:
                    raise TransactionError(f'参与服务不存在: {participant_name}')

            step = TransactionStep(
                StepUUID=uuid.uuid4().bytes,
                TransactionUUID=dtx,
                StepIndex=idx,
                StepName=step_data['name'],
                StepDescription=step_data.get('description', ''),
                ParticipantServiceUUID=participant,
                StepStatus=TransactionStep.STEP_STATUS_PENDING,
                ActionEndpoint=step_data.get('ActionEndpoint', ''),
                ActionMethod='POST',
                ActionPayload=step_data.get('ActionPayload', {}),
                CompensateEndpoint=step_data.get('CompensateEndpoint', ''),
                CompensateMethod='POST',
                CompensatePayload=step_data.get('CompensatePayload', {}),
                MaxRetries=step_data.get('MaxRetries', 3),
            )
            step.save()

        logger.info(f'分布式事务创建: {tx_code} ({name}), {len(steps_data)} 步骤')
        return dtx

    @staticmethod
    @db_transaction.atomic
    def Execute(transaction_code, access_token=None):
        """执行分布式事务（Saga 正向流程）

        按步骤顺序依次调用各工作服务的本地事务管理器，
        任一步骤失败则触发补偿流程。

        Args:
            transaction_code: 事务代码
            access_token: 服务间认证令牌

        Returns:
            DistributedTransaction 实例
        """
        try:
            dtx = DistributedTransaction.objects.select_for_update().get(TransactionCode=transaction_code)
        except DistributedTransaction.DoesNotExist:
            raise TransactionError(f'事务不存在: {transaction_code}')

        if dtx.TransactionStatus not in (
            DistributedTransaction.TX_STATUS_PENDING,
            DistributedTransaction.TX_STATUS_RUNNING,
        ):
            raise TransactionError(f'事务状态不允许执行: {dtx.get_TransactionStatus_display()}')

        if dtx.IsTimedOut:
            dtx.TransactionStatus = DistributedTransaction.TX_STATUS_TIMED_OUT
            dtx.UpdateTime = _utcnow()
            dtx.save(update_fields=['TransactionStatus', 'UpdateTime'])
            db_transaction.on_commit(
                lambda: TransactionManager.Compensate(transaction_code, access_token=access_token)
            )
            raise TransactionError(f'事务已超时: {transaction_code}')

        dtx.TransactionStatus = DistributedTransaction.TX_STATUS_RUNNING
        dtx.UpdateTime = _utcnow()
        dtx.save(update_fields=['TransactionStatus', 'UpdateTime'])

        steps = list(dtx.steps.all().order_by('StepIndex'))

        for step in steps:
            if step.StepStatus == TransactionStep.STEP_STATUS_COMPLETED:
                continue

            if dtx.IsTimedOut:
                dtx.TransactionStatus = DistributedTransaction.TX_STATUS_TIMED_OUT
                dtx.UpdateTime = _utcnow()
                dtx.save(update_fields=['TransactionStatus', 'UpdateTime'])
                db_transaction.on_commit(
                    lambda tc=transaction_code, at=access_token: TransactionManager.Compensate(tc, access_token=at)
                )
                raise TransactionError(f'事务执行超时: {transaction_code}')

            try:
                result = TransactionManager._execute_step(step, access_token=access_token)
                step.StepStatus = TransactionStep.STEP_STATUS_COMPLETED
                step.StepResult = result
                step.EndTime = _utcnow()
                step.save(update_fields=['StepStatus', 'StepResult', 'EndTime'])
            except StepExecutionError as e:
                step.StepStatus = TransactionStep.STEP_STATUS_FAILED
                step.StepResult = {
                    'error': str(e),
                    'status_code': e.StatusCode,
                    'response': e.ResponseBody,
                }
                step.EndTime = _utcnow()
                step.save(update_fields=['StepStatus', 'StepResult', 'EndTime'])

                logger.warning(f'事务步骤失败: {transaction_code} step={step.StepIndex}, 触发补偿')
                db_transaction.on_commit(
                    lambda tc=transaction_code, at=access_token: TransactionManager.Compensate(tc, access_token=at)
                )
                return DistributedTransaction.objects.get(TransactionCode=transaction_code)

        dtx.TransactionStatus = DistributedTransaction.TX_STATUS_COMMITTED
        dtx.EndTime = _utcnow()
        dtx.UpdateTime = _utcnow()
        dtx.save(update_fields=['TransactionStatus', 'EndTime', 'UpdateTime'])

        logger.info(f'分布式事务提交: {transaction_code}')
        return dtx

    @staticmethod
    @db_transaction.atomic
    def Compensate(transaction_code, access_token=None):
        """补偿分布式事务（Saga 反向流程）

        逆序遍历已完成步骤，调用各工作服务的本地事务管理器补偿端点。

        Args:
            transaction_code: 事务代码
            access_token: 服务间认证令牌
        """
        try:
            dtx = DistributedTransaction.objects.select_for_update().get(TransactionCode=transaction_code)
        except DistributedTransaction.DoesNotExist:
            raise TransactionError(f'事务不存在: {transaction_code}')

        if dtx.TransactionStatus == DistributedTransaction.TX_STATUS_COMPENSATED:
            return dtx

        dtx.TransactionStatus = DistributedTransaction.TX_STATUS_COMPENSATING
        dtx.UpdateTime = _utcnow()
        dtx.save(update_fields=['TransactionStatus', 'UpdateTime'])

        completed_steps = list(
            dtx.steps.filter(StepStatus=TransactionStep.STEP_STATUS_COMPLETED)
            .order_by('-StepIndex')
        )

        compensation_failed = False
        for step in completed_steps:
            step.StepStatus = TransactionStep.STEP_STATUS_COMPENSATING
            step.save(update_fields=['StepStatus'])

            try:
                result = TransactionManager._compensate_step(step, access_token=access_token)
                step.StepStatus = TransactionStep.STEP_STATUS_COMPENSATED
                step.StepResult.update({'compensate_result': result})
                step.EndTime = _utcnow()
                step.save(update_fields=['StepStatus', 'StepResult', 'EndTime'])
            except StepExecutionError as e:
                # 补偿失败：步骤置 FAILED（而非停留在 COMPENSATING），
                # 使 RetryCompensate 可再次补偿，避免事务永远补不回来
                compensation_failed = True
                step.StepStatus = TransactionStep.STEP_STATUS_FAILED
                step.StepResult.update({
                    'compensate_error': str(e),
                    'compensate_status_code': e.StatusCode,
                })
                step.EndTime = _utcnow()
                step.save(update_fields=['StepStatus', 'StepResult', 'EndTime'])
                logger.error(f'补偿步骤失败: {transaction_code} step={step.StepIndex}: {e}')

        if compensation_failed:
            dtx.TransactionStatus = DistributedTransaction.TX_STATUS_FAILED
        else:
            dtx.TransactionStatus = DistributedTransaction.TX_STATUS_COMPENSATED

        dtx.EndTime = _utcnow()
        dtx.UpdateTime = _utcnow()
        dtx.save(update_fields=['TransactionStatus', 'EndTime', 'UpdateTime'])

        logger.info(f'分布式事务补偿完成: {transaction_code} -> {dtx.get_TransactionStatus_display()}')
        return dtx

    @staticmethod
    def RetryStep(transaction_code, step_index, access_token=None):
        """重试失败步骤"""
        try:
            dtx = DistributedTransaction.objects.get(TransactionCode=transaction_code)
        except DistributedTransaction.DoesNotExist:
            raise TransactionError(f'事务不存在: {transaction_code}')

        try:
            step = dtx.steps.get(StepIndex=step_index)
        except TransactionStep.DoesNotExist:
            raise TransactionError(f'步骤不存在: {step_index}')

        if step.StepStatus not in (
            TransactionStep.STEP_STATUS_FAILED,
            TransactionStep.STEP_STATUS_PENDING,
        ):
            raise TransactionError(f'步骤状态不允许重试: {step.get_StepStatus_display()}')

        if not step.CanRetry:
            raise TransactionError(f'步骤已达到最大重试次数: {step.MaxRetries}')

        step.RetryCount += 1
        step.StepStatus = TransactionStep.STEP_STATUS_PENDING
        step.save(update_fields=['RetryCount', 'StepStatus'])

        try:
            result = TransactionManager._execute_step(step, access_token=access_token)
            step.StepStatus = TransactionStep.STEP_STATUS_COMPLETED
            step.StepResult = result
            step.EndTime = _utcnow()
            step.save(update_fields=['StepStatus', 'StepResult', 'EndTime'])
        except StepExecutionError as e:
            step.StepStatus = TransactionStep.STEP_STATUS_FAILED
            step.StepResult = {
                'error': str(e),
                'status_code': e.StatusCode,
                'response': e.ResponseBody,
                'retry_count': step.RetryCount,
            }
            step.EndTime = _utcnow()
            step.save(update_fields=['StepStatus', 'StepResult', 'EndTime'])

        return step

    @staticmethod
    @db_transaction.atomic
    def RetryCompensate(transaction_code, step_index, access_token=None):
        """重试补偿失败的步骤

        仅允许对补偿失败（StepResult 含 compensate_error）的步骤重试补偿；
        补偿成功后若不存在仍补偿失败的步骤，事务置 Compensated。

        Args:
            transaction_code: 事务代码
            step_index: 步骤序号
            access_token: 服务间认证令牌

        Returns:
            TransactionStep 实例
        """
        try:
            dtx = DistributedTransaction.objects.select_for_update().get(TransactionCode=transaction_code)
        except DistributedTransaction.DoesNotExist:
            raise TransactionError(f'事务不存在: {transaction_code}')

        if dtx.TransactionStatus == DistributedTransaction.TX_STATUS_COMPENSATED:
            raise TransactionError('事务已补偿完成')

        try:
            step = dtx.steps.get(StepIndex=step_index)
        except TransactionStep.DoesNotExist:
            raise TransactionError(f'步骤不存在: {step_index}')

        if step.StepStatus != TransactionStep.STEP_STATUS_FAILED:
            raise TransactionError(f'仅补偿失败的步骤可重试补偿: {step.get_StepStatus_display()}')
        if 'compensate_error' not in (step.StepResult or {}):
            raise TransactionError('该步骤为正向执行失败，请使用 RetryStep 重试执行')

        if not step.CanRetry:
            raise TransactionError(f'步骤已达到最大重试次数: {step.MaxRetries}')

        step.RetryCount += 1
        step.StepStatus = TransactionStep.STEP_STATUS_COMPENSATING
        step.save(update_fields=['RetryCount', 'StepStatus'])

        try:
            result = TransactionManager._compensate_step(step, access_token=access_token)
            step.StepStatus = TransactionStep.STEP_STATUS_COMPENSATED
            step.StepResult.update({'compensate_result': result})
            step.EndTime = _utcnow()
            step.save(update_fields=['StepStatus', 'StepResult', 'EndTime'])
        except StepExecutionError as e:
            step.StepStatus = TransactionStep.STEP_STATUS_FAILED
            step.StepResult.update({
                'compensate_error': str(e),
                'compensate_status_code': e.StatusCode,
            })
            step.EndTime = _utcnow()
            step.save(update_fields=['StepStatus', 'StepResult', 'EndTime'])
            logger.error(f'补偿重试失败: {transaction_code} step={step.StepIndex}: {e}')

            dtx.TransactionStatus = DistributedTransaction.TX_STATUS_FAILED
            dtx.UpdateTime = _utcnow()
            dtx.save(update_fields=['TransactionStatus', 'UpdateTime'])
            return step

        # 补偿成功：若仍存在补偿失败的步骤则事务保持 FAILED，否则 COMPENSATED
        remaining_failed = any(
            'compensate_error' in (s.StepResult or {})
            for s in dtx.steps.all()
        )
        if remaining_failed:
            dtx.TransactionStatus = DistributedTransaction.TX_STATUS_FAILED
        else:
            dtx.TransactionStatus = DistributedTransaction.TX_STATUS_COMPENSATED
        dtx.UpdateTime = _utcnow()
        dtx.save(update_fields=['TransactionStatus', 'UpdateTime'])

        logger.info(f'补偿重试完成: {transaction_code} step={step.StepIndex} -> {dtx.get_TransactionStatus_display()}')
        return step

    @staticmethod
    def GetDetail(transaction_code):
        """获取事务详情（含所有步骤）"""
        try:
            dtx = DistributedTransaction.objects.prefetch_related('steps').get(
                TransactionCode=transaction_code,
            )
        except DistributedTransaction.DoesNotExist:
            return None

        steps = []
        for step in dtx.steps.all().order_by('StepIndex'):
            steps.append({
                'Index': step.StepIndex,
                'Name': step.StepName,
                'Description': step.StepDescription,
                'Participant': step.ParticipantServiceUUID.ServiceName if step.ParticipantServiceUUID else None,
                'Status': step.get_StepStatus_display(),
                'StatusCode': step.StepStatus,
                'Result': step.StepResult,
                'RetryCount': step.RetryCount,
                'MaxRetries': step.MaxRetries,
                'StartTime': step.StartTime.isoformat() if step.StartTime else None,
                'EndTime': step.EndTime.isoformat() if step.EndTime else None,
            })

        return {
            'TransactionCode': dtx.TransactionCode,
            'Name': dtx.TransactionName,
            'Description': dtx.TransactionDescription,
            'Type': dtx.TransactionType,
            'Status': dtx.get_TransactionStatus_display(),
            'StatusCode': dtx.TransactionStatus,
            'Initiator': dtx.InitiatorServiceUUID.ServiceName if dtx.InitiatorServiceUUID else None,
            'Payload': dtx.TransactionPayload,
            'Result': dtx.TransactionResult,
            'TimeoutSeconds': dtx.TimeoutSeconds,
            'Steps': steps,
            'CreateTime': dtx.CreateTime.isoformat(),
            'EndTime': dtx.EndTime.isoformat() if dtx.EndTime else None,
        }

    @staticmethod
    def Cancel(transaction_code):
        """取消待执行的事务"""
        try:
            dtx = DistributedTransaction.objects.get(TransactionCode=transaction_code)
        except DistributedTransaction.DoesNotExist:
            raise TransactionError(f'事务不存在: {transaction_code}')

        if dtx.TransactionStatus != DistributedTransaction.TX_STATUS_PENDING:
            raise TransactionError(f'只有待执行状态的事务可以取消')

        dtx.TransactionStatus = DistributedTransaction.TX_STATUS_FAILED
        dtx.EndTime = _utcnow()
        dtx.save(update_fields=['TransactionStatus', 'EndTime'])

        dtx.steps.filter(StepStatus=TransactionStep.STEP_STATUS_PENDING).update(
            StepStatus=TransactionStep.STEP_STATUS_SKIPPED,
            EndTime=_utcnow(),
        )

        logger.info(f'分布式事务取消: {transaction_code}')
        return dtx

    @staticmethod
    def CheckTimeouts():
        """检测所有超时事务并触发补偿"""
        timed_out = []
        running_txs = DistributedTransaction.objects.filter(
            TransactionStatus__in=(
                DistributedTransaction.TX_STATUS_PENDING,
                DistributedTransaction.TX_STATUS_RUNNING,
            ),
        )

        for dtx in running_txs:
            if dtx.IsTimedOut:
                TransactionManager._mark_timed_out(dtx)
                try:
                    TransactionManager.Compensate(dtx.TransactionCode)
                except TransactionError:
                    pass
                timed_out.append(dtx.TransactionCode)

        if timed_out:
            logger.warning(f'检测到 {len(timed_out)} 个超时事务: {timed_out}')
        return timed_out

    @staticmethod
    def QueryByStatus(status, limit=50):
        """按状态查询事务"""
        txs = DistributedTransaction.objects.filter(
            TransactionStatus=status,
        ).order_by('-CreateTime')[:limit]

        return [
            {
                'TransactionCode': tx.TransactionCode,
                'Name': tx.TransactionName,
                'Type': tx.TransactionType,
                'Status': tx.get_TransactionStatus_display(),
                'Initiator': tx.InitiatorServiceUUID.ServiceName if tx.InitiatorServiceUUID else None,
                'CreateTime': tx.CreateTime.isoformat(),
                'EndTime': tx.EndTime.isoformat() if tx.EndTime else None,
            }
            for tx in txs
        ]

    # ─── 内部方法 ───────────────────────────────────────────

    @staticmethod
    def _mark_timed_out(dtx):
        dtx.TransactionStatus = DistributedTransaction.TX_STATUS_TIMED_OUT
        dtx.UpdateTime = _utcnow()
        dtx.save(update_fields=['TransactionStatus', 'UpdateTime'])

    @staticmethod
    def _get_service_domain(step):
        """获取步骤参与服务的域名"""
        if step.ParticipantServiceUUID:
            return step.ParticipantServiceUUID.ServiceDomain.rstrip('/')
        return None

    @staticmethod
    def _execute_step(step, access_token=None):
        """执行单个步骤——调用参与服务的本地事务管理器 execute 端点"""
        step.StepStatus = TransactionStep.STEP_STATUS_RUNNING
        step.StartTime = _utcnow()
        step.save(update_fields=['StepStatus', 'StartTime'])

        service_domain = TransactionManager._get_service_domain(step)
        if not service_domain:
            raise StepExecutionError(
                f'步骤无参与服务或服务域名未配置: step={step.StepIndex}',
                step_index=step.StepIndex,
            )

        endpoint = f'{service_domain}{LOCAL_EXECUTE_PATH}'
        payload = {
            'StepName': step.StepName,
            'payload': step.ActionPayload,
            'TransactionCode': step.TransactionUUID.TransactionCode,
            'StepIndex': step.StepIndex,
        }

        return TransactionManager._call_service(
            endpoint=endpoint,
            payload=payload,
            access_token=access_token,
        )

    @staticmethod
    def _compensate_step(step, access_token=None):
        """补偿单个步骤——调用参与服务的本地事务管理器 compensate 端点"""
        service_domain = TransactionManager._get_service_domain(step)
        if not service_domain:
            raise StepExecutionError(
                f'步骤无参与服务或服务域名未配置: step={step.StepIndex}',
                step_index=step.StepIndex,
            )

        endpoint = f'{service_domain}{LOCAL_COMPENSATE_PATH}'
        payload = {
            'StepName': step.StepName,
            'payload': step.CompensatePayload,
            'ActionResult': step.StepResult,
            'TransactionCode': step.TransactionUUID.TransactionCode,
            'StepIndex': step.StepIndex,
        }

        return TransactionManager._call_service(
            endpoint=endpoint,
            payload=payload,
            access_token=access_token,
        )

    @staticmethod
    def _call_service(endpoint, payload=None, access_token=None):
        """调用远程服务的本地事务管理器端点（使用连接池 + TLS 验证）"""
        headers = {'Content-Type': 'application/json'}
        if access_token:
            headers['Authorization'] = f'Service {access_token}'

        try:
            resp = _http_session.post(
                endpoint,
                json=payload or {},
                headers=headers,
                timeout=30,
            )
            response_data = resp.text

            # 非 2xx：无论响应体格式（HTML 错误页/网关页等）一律视为失败，
            # 避免步骤被误判为 Completed
            if not resp.ok:
                raise StepExecutionError(
                    f'本地事务管理器调用失败: HTTP {resp.status_code}',
                    status_code=resp.status_code,
                    response_body=response_data[:4096],
                )

            content_type = resp.headers.get('Content-Type', '')
            if 'application/json' in content_type:
                try:
                    result = resp.json()
                except ValueError:
                    # JSONDecodeError 是 ValueError 子类：响应体声明 JSON 却无法解析时，
                    # 转 StepExecutionError，保证失败必进补偿链路，事务不悬挂 Running
                    raise StepExecutionError(
                        f'本地事务管理器返回非法 JSON: {response_data[:512]}',
                        status_code=resp.status_code,
                        response_body=response_data[:4096],
                    )
                if isinstance(result, dict) and result.get('code') == 200:
                    return result.get('data', result)
                elif isinstance(result, dict) and 'success' in result:
                    if not result['success']:
                        raise StepExecutionError(
                            result.get('error', '本地执行失败'),
                            response_body=response_data,
                        )
                    return result.get('result', result)
                return result
            return {'raw': response_data}
        except requests.exceptions.RequestException as e:
            # 统一兜底：连接失败/超时/重试耗尽等任何 requests 异常
            # 一律转 StepExecutionError，确保失败必进补偿链路
            raise StepExecutionError(f'本地事务管理器调用失败: {e}')

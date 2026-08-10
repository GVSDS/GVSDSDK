import logging
import traceback
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from gvsdsdk.msync.auth import (
    AuthenticateService,
    RefreshServiceToken,
    ValidateAccessToken,
)
from gvsdsdk.msync.middleware import ServiceTokenAuthentication
from gvsdsdk.msync.registry import ServiceRegistry, RegistryError
from gvsdsdk.msync.ogm import OGMManager, OGMError
from gvsdsdk.msync.transaction import TransactionManager, TransactionError
from gvsdsdk.msync.local_tx import local_tx
from gvsdsdk.msync.serializers import (
    AuthTokenRequestSerializer,
    RefreshTokenRequestSerializer,
    ServiceRegisterSerializer,
    ServiceNodeSerializer,
    OGMRegisterSerializer,
    OGMBatchRegisterSerializer,
    ServiceTokenResponseSerializer,
    TransactionCreateSerializer,
    TransactionExecuteSerializer,
    TransactionRetryStepSerializer,
    LocalExecuteSerializer,
    LocalCompensateSerializer,
)

logger = logging.getLogger(__name__)


def _ok(data=None, msg='success'):
    return {'code': 200, 'msg': msg, 'data': data}


def _err(msg, code=400):
    return {'code': code, 'msg': msg, 'data': None}


class AuthTokenView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        try:
            ser = AuthTokenRequestSerializer(data=request.data)
            if not ser.IsValid():
                return Response(_err(ser.errors, 400), status=400)

            token = AuthenticateService(
                service_name=ser.ValidatedData['ServiceName'],
                signed_payload=ser.ValidatedData['Payload'],
                signature=ser.ValidatedData['Signature'],
            )
            if token is None:
                return Response(_err('认证失败', 401), status=401)

            try:
                scope = token.TokenScope
                if not isinstance(scope, list):
                    scope = list(scope) if scope else []
            except Exception:
                scope = []

            try:
                # naive UTC 需先补时区再 timestamp()，避免按本地时区解释偏移
                if isinstance(token.ExpiresAt, str):
                    from django.utils.dateparse import parse_datetime
                    dt = parse_datetime(token.ExpiresAt)
                    if dt is None:
                        raise ValueError
                else:
                    dt = token.ExpiresAt
                if dt.tzinfo is None:
                    import datetime as _dt
                    dt = dt.replace(tzinfo=_dt.timezone.utc)
                expires_ts = dt.timestamp()
            except Exception:
                import time as _time
                expires_ts = _time.time() + 7200

            data = ServiceTokenResponseSerializer({
                'AccessToken': token.AccessToken,
                'RefreshToken': token.RefreshToken,
                'Scope': scope,
                'ExpiresAt': expires_ts,
            }).data
            return Response(_ok(data))
        except Exception as e:
            logger.error(f'认证令牌签发异常: {e}\n{traceback.format_exc()}')
            return Response(_err('服务器内部错误', 500), status=500)


class AuthTokenRefreshView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        try:
            ser = RefreshTokenRequestSerializer(data=request.data)
            if not ser.IsValid():
                return Response(_err(ser.errors, 400), status=400)

            token = RefreshServiceToken(ser.ValidatedData['RefreshToken'])
            if token is None:
                return Response(_err('刷新失败', 401), status=401)

            try:
                scope = token.TokenScope
                if not isinstance(scope, list):
                    scope = list(scope) if scope else []
            except Exception:
                scope = []

            try:
                # naive UTC 需先补时区再 timestamp()，避免按本地时区解释偏移
                if isinstance(token.ExpiresAt, str):
                    from django.utils.dateparse import parse_datetime
                    dt = parse_datetime(token.ExpiresAt)
                    if dt is None:
                        raise ValueError
                else:
                    dt = token.ExpiresAt
                if dt.tzinfo is None:
                    import datetime as _dt
                    dt = dt.replace(tzinfo=_dt.timezone.utc)
                expires_ts = dt.timestamp()
            except Exception:
                import time as _time
                expires_ts = _time.time() + 7200

            data = ServiceTokenResponseSerializer({
                'AccessToken': token.AccessToken,
                'RefreshToken': token.RefreshToken,
                'Scope': scope,
                'ExpiresAt': expires_ts,
            }).data
            return Response(_ok(data))
        except Exception as e:
            logger.error(f'令牌刷新异常: {e}\n{traceback.format_exc()}')
            return Response(_err('服务器内部错误', 500), status=500)


class AuthTokenIntrospectView(APIView):
    """Token 自省端点 — 供子服务器验证 ServiceToken 有效性

    子服务器无法直接访问主服务器的 Django ORM 和 MASTER_SECRET，
    因此通过此端点远程验证 token。
    """
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        access_token = request.data.get('AccessToken', '')
        if not access_token:
            return Response(_err('AccessToken 不能为空', 400), status=400)

        try:
            payload = ValidateAccessToken(access_token)
            if payload is None:
                return Response({'Valid': False, 'Active': False})
            return Response({'Valid': True, 'Active': True, 'Payload': payload})
        except Exception as e:
            logger.error(f'Token 自省异常: {e}\n{traceback.format_exc()}')
            return Response({'Valid': False, 'Active': False})


class RegistryListView(APIView):
    authentication_classes = [ServiceTokenAuthentication]

    def get(self, request):
        if not request.user.is_authenticated:
            return Response(_err('需要服务认证', 401), status=401)
        services = ServiceRegistry.DiscoverAll(active_only=True)
        return Response(_ok(services))

    def post(self, request):
        # 注册端点：新服务尚未拥有令牌，无需认证，但必须携带密钥
        # TODO: 考虑添加速率限制以防止滥用注册
        ser = ServiceRegisterSerializer(data=request.data)
        if not ser.IsValid():
            return Response(_err(ser.errors, 400), status=400)

        try:
            node = ServiceRegistry.Register(
                name=ser.ValidatedData['ServiceName'],
                domain=ser.ValidatedData['ServiceDomain'],
                secret_hash=ser.ValidatedData.get('ServiceSecretHash', ''),
                raw_secret=ser.ValidatedData.get('ServiceSecret'),
                scope=ser.ValidatedData.get('ServiceScope'),
                service_type=ser.ValidatedData.get('ServiceType', 0),
                metadata=ser.ValidatedData.get('ServiceMetadata', {}),
            )
        except RegistryError as e:
            return Response(_err(str(e), 409), status=409)

        data = ServiceNodeSerializer(node).data
        return Response(_ok(data), status=201)


class RegistryDetailView(APIView):
    authentication_classes = [ServiceTokenAuthentication]

    def get(self, request, name):
        if not request.user.is_authenticated:
            return Response(_err('需要服务认证', 401), status=401)
        info = ServiceRegistry.Discover(name)
        if info is None:
            return Response(_err('服务不存在', 404), status=404)
        return Response(_ok(info))


class RegistryHeartbeatView(APIView):
    authentication_classes = [ServiceTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, name):
        # 仅允许持有该服务密钥签发的令牌执行本服务心跳
        if getattr(request, 'service_name', None) != name:
            return Response(_err('无权操作其他服务', 403), status=403)
        try:
            ServiceRegistry.Heartbeat(name)
        except RegistryError as e:
            return Response(_err(str(e), 404), status=404)
        return Response(_ok(msg='心跳成功'))


class RegistryDeregisterView(APIView):
    authentication_classes = [ServiceTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def delete(self, request, name):
        # 仅允许持有该服务密钥签发的令牌注销本服务
        if getattr(request, 'service_name', None) != name:
            return Response(_err('无权操作其他服务', 403), status=403)
        try:
            ServiceRegistry.Deregister(name)
        except RegistryError as e:
            return Response(_err(str(e), 404), status=404)
        return Response(_ok(msg='注销成功'))


class OGMRegisterView(APIView):
    authentication_classes = [ServiceTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = OGMRegisterSerializer(data=request.data)
        if not ser.IsValid():
            return Response(_err(ser.errors, 400), status=400)

        try:
            ogm = OGMManager.Register(
                object_type=ser.ValidatedData['ObjectType'],
                local_id=ser.ValidatedData['ObjectLocalID'],
                service_name=ser.ValidatedData['ServiceName'],
                global_id=ser.ValidatedData.get('ObjectGlobalID'),
                metadata=ser.ValidatedData.get('Metadata', {}),
            )
        except OGMError as e:
            return Response(_err(str(e), 409), status=409)

        return Response(_ok(OGMManager._to_dict(ogm)), status=201)


class OGMBatchRegisterView(APIView):
    authentication_classes = [ServiceTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = OGMBatchRegisterSerializer(data=request.data)
        if not ser.IsValid():
            return Response(_err(ser.errors, 400), status=400)

        try:
            results = OGMManager.RegisterBatch(
                items=ser.ValidatedData['Items'],
                service_name=ser.ValidatedData['ServiceName'],
            )
        except OGMError as e:
            return Response(_err(str(e), 409), status=409)

        data = [OGMManager._to_dict(ogm) for ogm in results]
        return Response(_ok(data), status=201)


class OGMResolveView(APIView):
    authentication_classes = [ServiceTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, global_id):
        info = OGMManager.Resolve(global_id)
        if info is None:
            return Response(_err('映射不存在', 404), status=404)
        return Response(_ok(info))


class OGMQueryView(APIView):
    authentication_classes = [ServiceTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        ObjectType = request.query_params.get('ObjectType')
        ServiceName = request.query_params.get('ServiceName')
        LocalID = request.query_params.get('LocalID')

        if LocalID and ObjectType and ServiceName:
            info = OGMManager.ResolveLocal(ObjectType, LocalID, ServiceName)
            if info is None:
                return Response(_err('映射不存在', 404), status=404)
            return Response(_ok(info))

        if ObjectType:
            results = OGMManager.QueryByType(ObjectType)
            return Response(_ok(results))

        if ServiceName:
            results = OGMManager.QueryByService(ServiceName, object_type=ObjectType)
            return Response(_ok(results))

        return Response(_err('请提供 ObjectType 或 ServiceName 参数', 400), status=400)


class OGMUpdateView(APIView):
    authentication_classes = [ServiceTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def put(self, request, global_id):
        metadata = request.data.get('Metadata')
        ogm = OGMManager.Update(global_id, metadata=metadata)
        if ogm is None:
            return Response(_err('映射不存在', 404), status=404)
        return Response(_ok(OGMManager._to_dict(ogm)))


class OGMDeregisterView(APIView):
    authentication_classes = [ServiceTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def delete(self, request, global_id):
        success = OGMManager.Deregister(global_id)
        if not success:
            return Response(_err('映射不存在', 404), status=404)
        return Response(_ok(msg='注销成功'))


class TransactionCreateView(APIView):
    authentication_classes = [ServiceTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = TransactionCreateSerializer(data=request.data)
        if not ser.IsValid():
            return Response(_err(ser.errors, 400), status=400)

        try:
            dtx = TransactionManager.Create(
                name=ser.ValidatedData['TransactionName'],
                steps_data=ser.ValidatedData['Steps'],
                description=ser.ValidatedData.get('TransactionDescription', ''),
                transaction_type=ser.ValidatedData.get('TransactionType', 'saga'),
                initiator_service_name=ser.ValidatedData.get('InitiatorServiceName'),
                reference_ogm_uuid=ser.ValidatedData.get('ReferenceOGMUUID'),
                payload=ser.ValidatedData.get('Payload'),
                timeout_seconds=ser.ValidatedData.get('TimeoutSeconds', 300),
            )
        except TransactionError as e:
            return Response(_err(str(e), 400), status=400)

        return Response(_ok(TransactionManager.GetDetail(dtx.TransactionCode)), status=201)


class TransactionExecuteView(APIView):
    authentication_classes = [ServiceTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, transaction_code):
        ser = TransactionExecuteSerializer(data=request.data)
        if not ser.IsValid():
            return Response(_err(ser.errors, 400), status=400)

        try:
            dtx = TransactionManager.Execute(
                transaction_code,
                access_token=ser.ValidatedData.get('AccessToken'),
            )
        except TransactionError as e:
            return Response(_err(str(e), 400), status=400)

        return Response(_ok(TransactionManager.GetDetail(dtx.TransactionCode)))


class TransactionCompensateView(APIView):
    authentication_classes = [ServiceTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, transaction_code):
        access_token = request.data.get('AccessToken')
        try:
            dtx = TransactionManager.Compensate(
                transaction_code,
                access_token=access_token,
            )
        except TransactionError as e:
            return Response(_err(str(e), 400), status=400)

        return Response(_ok(TransactionManager.GetDetail(dtx.TransactionCode)))


class TransactionCancelView(APIView):
    authentication_classes = [ServiceTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, transaction_code):
        try:
            TransactionManager.Cancel(transaction_code)
        except TransactionError as e:
            return Response(_err(str(e), 400), status=400)

        return Response(_ok(msg='事务已取消'))


class TransactionDetailView(APIView):
    authentication_classes = [ServiceTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, transaction_code):
        detail = TransactionManager.GetDetail(transaction_code)
        if detail is None:
            return Response(_err('事务不存在', 404), status=404)
        return Response(_ok(detail))


class TransactionRetryStepView(APIView):
    authentication_classes = [ServiceTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, transaction_code):
        ser = TransactionRetryStepSerializer(data=request.data)
        if not ser.IsValid():
            return Response(_err(ser.errors, 400), status=400)

        try:
            step = TransactionManager.RetryStep(
                transaction_code,
                step_index=ser.ValidatedData['StepIndex'],
                access_token=ser.ValidatedData.get('AccessToken'),
            )
        except TransactionError as e:
            return Response(_err(str(e), 400), status=400)

        return Response(_ok({
            'StepIndex': step.StepIndex,
            'StepName': step.StepName,
            'Status': step.get_StepStatus_display(),
            'RetryCount': step.RetryCount,
        }))


class TransactionRetryCompensateView(APIView):
    authentication_classes = [ServiceTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, transaction_code):
        ser = TransactionRetryStepSerializer(data=request.data)
        if not ser.IsValid():
            return Response(_err(ser.errors, 400), status=400)

        try:
            step = TransactionManager.RetryCompensate(
                transaction_code,
                step_index=ser.ValidatedData['StepIndex'],
                access_token=ser.ValidatedData.get('AccessToken'),
            )
        except TransactionError as e:
            return Response(_err(str(e), 400), status=400)

        return Response(_ok({
            'StepIndex': step.StepIndex,
            'StepName': step.StepName,
            'Status': step.get_StepStatus_display(),
            'RetryCount': step.RetryCount,
            'TransactionStatus': step.TransactionUUID.get_TransactionStatus_display(),
        }))


class TransactionTimeoutCheckView(APIView):
    authentication_classes = [ServiceTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        token = getattr(request.user, 'service_token', None)
        timed_out = TransactionManager.CheckTimeouts(access_token=token)
        return Response(_ok({'TimedOut': timed_out, 'Count': len(timed_out)}))


class TransactionQueryView(APIView):
    authentication_classes = [ServiceTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        status = request.query_params.get('status')
        if status is None:
            return Response(_err('请提供 status 参数', 400), status=400)

        try:
            status_int = int(status)
        except ValueError:
            return Response(_err('status 必须为整数', 400), status=400)

        results = TransactionManager.QueryByStatus(status_int)
        return Response(_ok(results))


class LocalExecuteView(APIView):
    authentication_classes = [ServiceTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = LocalExecuteSerializer(data=request.data)
        if not ser.IsValid():
            return Response(_err(ser.errors, 400), status=400)

        result = local_tx.ExecuteStep(
            name=ser.ValidatedData['StepName'],
            payload=ser.ValidatedData.get('Payload'),
            idempotency_key=request.headers.get('Idempotency-Key'),
        )

        if result['success']:
            return Response(_ok(result['result']))
        return Response(_err(result['error'], 500), status=500)


class LocalCompensateView(APIView):
    authentication_classes = [ServiceTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = LocalCompensateSerializer(data=request.data)
        if not ser.IsValid():
            return Response(_err(ser.errors, 400), status=400)

        result = local_tx.CompensateStep(
            name=ser.ValidatedData['StepName'],
            payload=ser.ValidatedData.get('Payload'),
            action_result=ser.ValidatedData.get('ActionResult'),
            idempotency_key=request.headers.get('Idempotency-Key'),
        )

        if result['success']:
            return Response(_ok(result['result']))
        return Response(_err(result['error'], 500), status=500)


class LocalHandlersView(APIView):
    authentication_classes = [ServiceTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        handlers = local_tx.ListHandlers()
        return Response(_ok(handlers))

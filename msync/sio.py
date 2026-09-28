"""模块：MSYNC 服务端（Socket.IO）

替代 REST API，提供实时双向通信。事件协议字段全部使用 PascalCase。

事件协议:
  客户端 → 服务端:
    msync:register          注册服务（bootstrap，必须携带密钥）
    msync:auth              HMAC 签名认证（密钥验证，成功后才可调用业务事件）
    msync:auth:refresh      刷新令牌
    msync:heartbeat         应用层心跳（需已认证）
    msync:deregister        注销服务（需已认证，仅限本服务）
    msync:discover          发现所有服务（需已认证）
    msync:discover:one      发现单个服务（需已认证）
    msync:ogm:register      注册 OGM（需已认证）
    msync:ogm:batch         批量注册 OGM（需已认证）
    msync:ogm:resolve       解析 OGM（需已认证）
    msync:ogm:query         查询 OGM（需已认证）
    msync:ogm:update        更新 OGM（需已认证）
    msync:ogm:deregister    注销 OGM（需已认证）
    msync:tx:create         创建事务（需已认证）
    msync:tx:execute        执行事务（需已认证）
    msync:tx:compensate     补偿事务（需已认证）
    msync:tx:cancel         取消事务（需已认证）
    msync:tx:detail         事务详情（需已认证）
    msync:tx:retry          重试步骤（需已认证）
    msync:tx:retry_compensate 重试补偿（需已认证）
    msync:tx:timeout_check  超时检测（需已认证）
    msync:tx:query          按状态查询（需已认证）

  鉴权规则: 除 register / auth / auth:refresh 外，所有业务事件必须
  先通过 msync:auth 密钥认证（HMAC 签名验证），否则返回 401。

  服务端 → 客户端（推送）:
    msync:service:online    服务上线
    msync:service:offline   服务下线
"""

import logging
import threading
import traceback
import urllib.parse
import socketio

logger = logging.getLogger(__name__)

NS = '/msync'

#: 允许的前端来源根域（含其全部子域）——浏览器 WebSocket 握手会带 Origin，必须显式放行
ALLOWED_ORIGIN_ROOTS = (
    'gvsds.com', 'gvsds.dev', 'gvsds.org', 'gvsds.studio', 'yc8z.org', 'ycbz.org',
)
#: 本地开发来源主机（任意端口）
ALLOWED_ORIGIN_HOSTS = ('localhost', '127.0.0.1')


def AllowOrigin(origin, environ=None):
    """CORS Origin 校验（供 engineio 以可调用对象方式调用）

    背景：engineio 的 cors_allowed_origins 只支持「精确字符串 / '*' / 可调用对象」，
    不支持 'https://*.gvsds.com' 这类通配写法——那样写会被当成字面量，
    浏览器端（如 https://gerp.gvsds.com）握手会被判为 "not an accepted origin"。
    这里按根域后缀统一放行，涵盖 gerp/www/api/viper 等全部子域。
    """
    if not origin:
        return False
    try:
        host = (urllib.parse.urlsplit(origin).hostname or '').lower()
    except ValueError:
        return False
    if not host:
        return False
    if host in ALLOWED_ORIGIN_HOSTS:
        return True
    return any(host == root or host.endswith('.' + root) for root in ALLOWED_ORIGIN_ROOTS)


sio = socketio.Server(async_mode='gevent', cors_allowed_origins=AllowOrigin,
                      logger=True, engineio_logger=True)

_sid_service_map = {}
_service_sid_map = {}
_sid_auth_map = {}
_map_lock = threading.Lock()


def _ok(data=None, msg='success'):
    return {'code': 200, 'msg': msg, 'data': data}


def _err(msg, code=400):
    return {'code': code, 'msg': msg, 'data': None}


def _d(data, key, default=None):
    """安全获取字典字段"""
    if not isinstance(data, dict):
        return default
    return data.get(key, default)


def _RequireAuth(sid):
    """校验 sid 是否已完成密钥认证（msync:auth）；通过返回服务名，否则返回 None"""
    with _map_lock:
        return _sid_auth_map.get(sid)


@sio.on('connect', namespace=NS)
def on_connect(sid, environ):
    logger.info(f'[SIO] → connect sid={sid}')


@sio.on('disconnect', namespace=NS)
def on_disconnect(sid):
    with _map_lock:
        service_name = _sid_service_map.pop(sid, None)
        if service_name:
            _service_sid_map.pop(service_name, None)
        _sid_auth_map.pop(sid, None)
    if service_name:
        try:
            from gvsdsdk.msync.registry import ServiceRegistry
            ServiceRegistry.Deregister(service_name)
        except Exception:
            pass
        # 子服务器下线：标记其提供的远端 App 为离线（gvsds 侧，惰性导入）
        try:
            from gvsds.apps.gerp.app_manifests import MarkServiceAppsOffline
            MarkServiceAppsOffline(service_name)
        except ImportError:
            pass
        sio.emit('msync:service:offline', {'Service': service_name}, namespace=NS, skip_sid=sid)
        logger.info(f'[SIO] 服务断开: {service_name} ({sid})')
    else:
        logger.info(f'[SIO] 客户端断开: {sid}')


@sio.on('msync:register', namespace=NS)
def on_register(sid, data):
    logger.info(f'[SIO] → msync:register sid={sid} name={_d(data, "ServiceName")}')
    from gvsdsdk.msync.registry import ServiceRegistry, RegistryError
    try:
        node = ServiceRegistry.Register(
            name=_d(data, 'ServiceName', ''),
            domain=_d(data, 'ServiceDomain', ''),
            secret_hash=_d(data, 'ServiceSecretHash', ''),
            raw_secret=_d(data, 'ServiceSecret'),
            scope=_d(data, 'ServiceScope'),
            service_type=_d(data, 'ServiceType', 0),
            metadata=_d(data, 'ServiceMetadata', {}),
        )
    except RegistryError as e:
        return _err(str(e), 409)
    except Exception as e:
        logger.error(f'注册异常: {e}\n{traceback.format_exc()}')
        return _err(str(e), 500)

    service_name = _d(data, 'ServiceName')
    with _map_lock:
        _sid_service_map[sid] = service_name
        _service_sid_map[service_name] = sid
        # 若以不同服务名重新注册，旧认证状态失效
        if _sid_auth_map.get(sid) != service_name:
            _sid_auth_map.pop(sid, None)

    sio.emit('msync:service:online', {
        'ServiceName': service_name,
        'ServiceDomain': _d(data, 'ServiceDomain'),
    }, namespace=NS, skip_sid=sid)

    from gvsdsdk.msync.serializers import ServiceNodeSerializer
    result = _ok(ServiceNodeSerializer(node).data)
    logger.info(f'[SIO] ← msync:register sid={sid} code=200')
    return result


@sio.on('msync:auth', namespace=NS)
def on_auth(sid, data):
    service_name = _d(data, 'ServiceName')
    logger.info(f'[SIO] → msync:auth sid={sid} service={service_name}')
    from gvsdsdk.msync.auth import AuthenticateService
    try:
        token = AuthenticateService(
            service_name=service_name,
            signed_payload=_d(data, 'Payload'),
            signature=_d(data, 'Signature'),
        )
        if token is None:
            return _err('认证失败', 401)

        try:
            scope = token.TokenScope
            if not isinstance(scope, list):
                scope = list(scope) if scope else []
        except Exception:
            scope = []

        try:
            from django.utils.dateparse import parse_datetime
            if isinstance(token.ExpiresAt, str):
                dt = parse_datetime(token.ExpiresAt)
                if dt is None:
                    raise ValueError
            else:
                dt = token.ExpiresAt
            # 若是 naive datetime，按 UTC 处理（DB 存的就是 naive UTC）
            if dt.tzinfo is None:
                import datetime as _dt
                dt = dt.replace(tzinfo=_dt.timezone.utc)
            expires_ts = dt.timestamp()
        except Exception:
            import time as _time
            expires_ts = _time.time() + 7200

        with _map_lock:
            _sid_auth_map[sid] = service_name

        return _ok({
            'AccessToken': token.AccessToken,
            'RefreshToken': token.RefreshToken,
            'Scope': scope,
            'ExpiresAt': expires_ts,
        })
    except Exception as e:
        logger.error(f'[SIO] msync:auth 异常: {e}\n{traceback.format_exc()}')
        return _err(str(e), 500)


@sio.on('msync:auth:refresh', namespace=NS)
def on_auth_refresh(sid, data):
    from gvsdsdk.msync.auth import RefreshServiceToken
    try:
        token = RefreshServiceToken(_d(data, 'RefreshToken', ''))
        if token is None:
            return _err('刷新失败', 401)

        try:
            scope = token.TokenScope
            if not isinstance(scope, list):
                scope = list(scope) if scope else []
        except Exception:
            scope = []

        try:
            from django.utils.dateparse import parse_datetime
            if isinstance(token.ExpiresAt, str):
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

        with _map_lock:
            _sid_auth_map[sid] = token.ServiceUUID.ServiceName

        return _ok({
            'AccessToken': token.AccessToken,
            'RefreshToken': token.RefreshToken,
            'Scope': scope,
            'ExpiresAt': expires_ts,
        })
    except Exception as e:
        logger.error(f'Socket令牌刷新异常: {e}\n{traceback.format_exc()}')
        return _err(str(e), 500)


@sio.on('msync:heartbeat', namespace=NS)
def on_heartbeat(sid, data):
    with _map_lock:
        service_name = _sid_service_map.get(sid)
        auth_name = _sid_auth_map.get(sid)
    if not service_name:
        return _err('未注册', 401)
    if not auth_name or auth_name != service_name:
        return _err('需要密钥认证', 401)
    from gvsdsdk.msync.registry import ServiceRegistry, RegistryError
    try:
        ServiceRegistry.Heartbeat(service_name)
    except RegistryError as e:
        return _err(str(e), 404)
    return _ok(msg='心跳成功')


@sio.on('msync:deregister', namespace=NS)
def on_deregister(sid, data):
    name = _d(data, 'ServiceName') or ''
    if not name:
        return _err('未指定服务名', 400)
    # 先鉴权（防止伪造 ServiceName 注销他人/污染连接状态），通过后再清映射
    with _map_lock:
        auth_name = _sid_auth_map.get(sid)
    if not auth_name or auth_name != name:
        return _err('需要密钥认证', 401)
    with _map_lock:
        _sid_service_map.pop(sid, None)
        if _service_sid_map.get(name) == sid:
            _service_sid_map.pop(name, None)
        _sid_auth_map.pop(sid, None)
    from gvsdsdk.msync.registry import ServiceRegistry, RegistryError
    try:
        ServiceRegistry.Deregister(name)
    except RegistryError as e:
        return _err(str(e), 404)
    # 子服务器注销：标记其提供的远端 App 为离线（gvsds 侧，惰性导入）
    try:
        from gvsds.apps.gerp.app_manifests import MarkServiceAppsOffline
        MarkServiceAppsOffline(name)
    except ImportError:
        pass
    sio.emit('msync:service:offline', {'Service': name}, namespace=NS, skip_sid=sid)
    return _ok(msg='注销成功')


@sio.on('msync:discover', namespace=NS)
def on_discover(sid, data):
    if not _RequireAuth(sid):
        return _err('需要密钥认证', 401)
    from gvsdsdk.msync.registry import ServiceRegistry
    active_only = _d(data, 'ActiveOnly', True)
    if not isinstance(active_only, bool):
        active_only = True
    services = ServiceRegistry.DiscoverAll(active_only=active_only)
    return _ok(services)


@sio.on('msync:discover:one', namespace=NS)
def on_discover_one(sid, data):
    if not _RequireAuth(sid):
        return _err('需要密钥认证', 401)
    from gvsdsdk.msync.registry import ServiceRegistry
    info = ServiceRegistry.Discover(_d(data, 'ServiceName', ''))
    if info is None:
        return _err('服务不存在', 404)
    return _ok(info)


@sio.on('msync:ogm:register', namespace=NS)
def on_ogm_register(sid, data):
    if not _RequireAuth(sid):
        return _err('需要密钥认证', 401)
    from gvsdsdk.msync.ogm import OGMManager, OGMError
    try:
        ogm = OGMManager.Register(
            object_type=_d(data, 'ObjectType'),
            local_id=_d(data, 'ObjectLocalID'),
            service_name=_d(data, 'ServiceName'),
            global_id=_d(data, 'ObjectGlobalID'),
            metadata=_d(data, 'Metadata', {}),
        )
    except OGMError as e:
        return _err(str(e), 409)
    except Exception as e:
        logger.error(f'OGM注册异常: {e}\n{traceback.format_exc()}')
        return _err(str(e), 500)
    return _ok(OGMManager._to_dict(ogm))


@sio.on('msync:ogm:batch', namespace=NS)
def on_ogm_batch(sid, data):
    if not _RequireAuth(sid):
        return _err('需要密钥认证', 401)
    from gvsdsdk.msync.ogm import OGMManager, OGMError
    try:
        results = OGMManager.RegisterBatch(
            items=_d(data, 'Items', []),
            service_name=_d(data, 'ServiceName'),
        )
    except OGMError as e:
        return _err(str(e), 409)
    return _ok([OGMManager._to_dict(o) for o in results])


@sio.on('msync:ogm:resolve', namespace=NS)
def on_ogm_resolve(sid, data):
    if not _RequireAuth(sid):
        return _err('需要密钥认证', 401)
    from gvsdsdk.msync.ogm import OGMManager
    info = OGMManager.Resolve(_d(data, 'ObjectGlobalID', ''))
    if info is None:
        return _err('映射不存在', 404)
    return _ok(info)


@sio.on('msync:ogm:query', namespace=NS)
def on_ogm_query(sid, data):
    if not _RequireAuth(sid):
        return _err('需要密钥认证', 401)
    from gvsdsdk.msync.ogm import OGMManager
    object_type = _d(data, 'ObjectType')
    service_name = _d(data, 'ServiceName')
    local_id = _d(data, 'ObjectLocalID')

    if local_id and object_type and service_name:
        info = OGMManager.ResolveLocal(object_type, local_id, service_name)
        if info is None:
            return _err('映射不存在', 404)
        return _ok(info)

    if object_type:
        results = OGMManager.QueryByType(object_type)
        return _ok(results)

    if service_name:
        results = OGMManager.QueryByService(service_name, object_type=object_type)
        return _ok(results)

    return _err('请提供 ObjectType 或 ServiceName 参数', 400)


@sio.on('msync:ogm:update', namespace=NS)
def on_ogm_update(sid, data):
    if not _RequireAuth(sid):
        return _err('需要密钥认证', 401)
    from gvsdsdk.msync.ogm import OGMManager
    ogm = OGMManager.Update(_d(data, 'ObjectGlobalID', ''), metadata=_d(data, 'Metadata'))
    if ogm is None:
        return _err('映射不存在', 404)
    return _ok(OGMManager._to_dict(ogm))


@sio.on('msync:ogm:deregister', namespace=NS)
def on_ogm_deregister(sid, data):
    if not _RequireAuth(sid):
        return _err('需要密钥认证', 401)
    from gvsdsdk.msync.ogm import OGMManager
    success = OGMManager.Deregister(_d(data, 'ObjectGlobalID', ''))
    if not success:
        return _err('映射不存在', 404)
    return _ok(msg='注销成功')


@sio.on('msync:tx:create', namespace=NS)
def on_tx_create(sid, data):
    if not _RequireAuth(sid):
        return _err('需要密钥认证', 401)
    from gvsdsdk.msync.transaction import TransactionManager, TransactionError
    try:
        dtx = TransactionManager.Create(
            name=_d(data, 'TransactionName'),
            steps_data=_d(data, 'Steps'),
            description=_d(data, 'TransactionDescription', ''),
            transaction_type=_d(data, 'TransactionType', 'saga'),
            initiator_service_name=_d(data, 'InitiatorServiceName'),
            reference_ogm_uuid=_d(data, 'ReferenceOGMUUID'),
            payload=_d(data, 'Payload'),
            timeout_seconds=_d(data, 'TimeoutSeconds', 300),
        )
    except TransactionError as e:
        return _err(str(e), 400)
    return _ok(TransactionManager.GetDetail(dtx.TransactionCode))


@sio.on('msync:tx:execute', namespace=NS)
def on_tx_execute(sid, data):
    if not _RequireAuth(sid):
        return _err('需要密钥认证', 401)
    from gvsdsdk.msync.transaction import TransactionManager, TransactionError
    try:
        dtx = TransactionManager.Execute(
            _d(data, 'TransactionCode', ''),
            access_token=_d(data, 'AccessToken'),
        )
    except TransactionError as e:
        return _err(str(e), 400)
    return _ok(TransactionManager.GetDetail(dtx.TransactionCode))


@sio.on('msync:tx:compensate', namespace=NS)
def on_tx_compensate(sid, data):
    if not _RequireAuth(sid):
        return _err('需要密钥认证', 401)
    from gvsdsdk.msync.transaction import TransactionManager, TransactionError
    try:
        dtx = TransactionManager.Compensate(
            _d(data, 'TransactionCode', ''),
            access_token=_d(data, 'AccessToken'),
        )
    except TransactionError as e:
        return _err(str(e), 400)
    return _ok(TransactionManager.GetDetail(dtx.TransactionCode))


@sio.on('msync:tx:cancel', namespace=NS)
def on_tx_cancel(sid, data):
    if not _RequireAuth(sid):
        return _err('需要密钥认证', 401)
    from gvsdsdk.msync.transaction import TransactionManager, TransactionError
    try:
        TransactionManager.Cancel(_d(data, 'TransactionCode', ''))
    except TransactionError as e:
        return _err(str(e), 400)
    return _ok(msg='事务已取消')


@sio.on('msync:tx:detail', namespace=NS)
def on_tx_detail(sid, data):
    if not _RequireAuth(sid):
        return _err('需要密钥认证', 401)
    from gvsdsdk.msync.transaction import TransactionManager
    detail = TransactionManager.GetDetail(_d(data, 'TransactionCode', ''))
    if detail is None:
        return _err('事务不存在', 404)
    return _ok(detail)


@sio.on('msync:tx:retry', namespace=NS)
def on_tx_retry(sid, data):
    if not _RequireAuth(sid):
        return _err('需要密钥认证', 401)
    from gvsdsdk.msync.transaction import TransactionManager, TransactionError
    try:
        step = TransactionManager.RetryStep(
            _d(data, 'TransactionCode', ''),
            step_index=_d(data, 'StepIndex', 0),
            access_token=_d(data, 'AccessToken'),
        )
    except TransactionError as e:
        return _err(str(e), 400)
    return _ok({
        'StepIndex': step.StepIndex,
        'StepName': step.StepName,
        'Status': step.get_StepStatus_display(),
        'RetryCount': step.RetryCount,
    })


@sio.on('msync:tx:retry_compensate', namespace=NS)
def on_tx_retry_compensate(sid, data):
    if not _RequireAuth(sid):
        return _err('需要密钥认证', 401)
    from gvsdsdk.msync.transaction import TransactionManager, TransactionError
    try:
        step = TransactionManager.RetryCompensate(
            _d(data, 'TransactionCode', ''),
            step_index=_d(data, 'StepIndex', 0),
            access_token=_d(data, 'AccessToken'),
        )
    except TransactionError as e:
        return _err(str(e), 400)
    return _ok({
        'StepIndex': step.StepIndex,
        'StepName': step.StepName,
        'Status': step.get_StepStatus_display(),
        'RetryCount': step.RetryCount,
        'TransactionStatus': step.TransactionUUID.get_TransactionStatus_display(),
    })


@sio.on('msync:tx:timeout_check', namespace=NS)
def on_tx_timeout_check(sid, data):
    if not _RequireAuth(sid):
        return _err('需要密钥认证', 401)
    from gvsdsdk.msync.transaction import TransactionManager
    timed_out = TransactionManager.CheckTimeouts()
    return _ok({'TimedOut': timed_out, 'Count': len(timed_out)})


@sio.on('msync:tx:query', namespace=NS)
def on_tx_query(sid, data):
    if not _RequireAuth(sid):
        return _err('需要密钥认证', 401)
    from gvsdsdk.msync.transaction import TransactionManager
    status = _d(data, 'Status')
    if status is None:
        return _err('请提供 Status 参数', 400)
    try:
        status_int = int(status)
    except (ValueError, TypeError):
        return _err('Status 必须为整数', 400)
    results = TransactionManager.QueryByStatus(status_int)
    return _ok(results)


@sio.on('msync:app:manifest:report', namespace=NS)
def on_app_manifest_report(sid, data):
    """子服务器上报应用清单（远端 App 注入）

    请求体：{ServiceName, ServiceDomain, Manifests: [AppManifest dict]}
    响应：{code, data: {Registered: [AppCode], Failed: [{AppCode, Reason}]}}

    处理委托主服务器 gvsds.apps.gerp.app_manifests.SyncRemoteAppManifests
    （惰性导入，gvsdsdk 独立部署时返回 501）。
    """
    if not _RequireAuth(sid):
        return _err('需要密钥认证', 401)
    with _map_lock:
        service_name = _sid_service_map.get(sid)
    if not service_name:
        return _err('服务未注册', 401)
    manifests = _d(data, 'Manifests', [])
    if not isinstance(manifests, list):
        return _err('Manifests 必须为列表', 400)
    service_domain = _d(data, 'ServiceDomain', '') or ''
    try:
        from gvsds.apps.gerp.app_manifests import SyncRemoteAppManifests
    except ImportError:
        return _err('主服务器未启用远端清单注册（gvsds 未安装）', 501)
    try:
        result = SyncRemoteAppManifests(
            manifests, service_name, service_domain,
        )
    except Exception as e:
        logger.error(f'远端清单同步异常: {e}\n{traceback.format_exc()}')
        return _err(str(e), 500)
    return _ok(result)


def create_sio_app(django_app):
    # 注册用户级命名空间 /talk（导入即完成事件注册）
    try:
        import gvsdsdk.talk_socket  # noqa: F401
    except Exception:
        logger.exception('[SIO] talk 命名空间注册失败')
    return socketio.WSGIApp(sio, django_app)

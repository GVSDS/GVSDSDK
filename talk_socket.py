"""模块：Talk 用户级 Socket.IO 命名空间（/talk）

面向企业聊天（Talk）的终端用户实时通道。与同文件体系中的 /msync 命名空间不同：
/msync 面向“服务”，鉴权是服务密钥 HMAC，没有终端用户身份；/talk 面向“用户”，
鉴权是用户 JWT，身份态在连接建立时解析并绑定到 sid。

事件协议（字段全部 PascalCase）:
  客户端 → 服务端:
    connect                 （握手，从 Cookie 或 auth.Token 尝试认证）
    talk:auth       {Token}                                    用户令牌认证（Cookie 不可用时的兜底）
    talk:send       {ConversationUUID, MsgType, Content,
                     AttachmentSHA1, AttachmentName,
                     AttachmentSize, AttachmentMime}           发送消息（需已认证）
    talk:read       {ConversationUUID, LastReadMessageUUID}    标记已读（需已认证）
    talk:typing     {ConversationUUID}                         正在输入（需已认证）
    talk:ping                                                  应用层心跳（需已认证）

  服务端 → 客户端（推送/响应）:
    talk:ready      _ok({UserUUID, CompanyUUID})               认证成功（连接或 talk:auth 后）
    talk:message    _ok(消息 dict)                             会话内新消息广播
    talk:read       _ok({ConversationUUID, UserUUID,
                         LastReadMessageUUID})                 已读回执广播
    talk:typing     _ok({ConversationUUID, UserUUID})          输入中广播（跳过发起者）
    talk:pong       _ok()                                      心跳响应
    talk:error      _err(...)                                  处理异常时单独推给触发的 sid

鉴权链路（与 HTTP 侧完全一致，不另写一套）:
    Cookie AccessToken(JWT) --VerifyUserToken--> UserUUID/CompanyUUID/TenantUUID/RoleUUID
    Cookie CurrentRoleUUID  --ResolveRoleContext--> current_role / current_company_uuid
                                                    / current_tenant_uuid
    其中角色/公司/租户上下文复用 gvsdsdk.auth.middleware.ResolveRoleContext（唯一实现），
    通过一个轻量 shim 对象代替 Django HttpRequest 传入。
    令牌用 SECRET_KEY 作为 HMAC 密钥，算法 HS256（见 IssueUserToken），有效期 12 小时。

单进程限制（重要）:
    python-socketio 运行在 async_mode='gevent' 的单进程内，且未配置 Redis / 消息队列
    adapter。因此：
      * 本模块的 _sid_user_map、会话房间（room）均只存在于当前进程内存；
      * BroadcastToConversation / BroadcastToUser 只能触达“连接到本进程”的 socket，
        无法跨 worker 广播；
      * 若未来以多 worker/多进程部署（如 gunicorn -w N、gevent 多实例），必须引入
        socketio.RedisManager（或其它 adapter）才能实现跨进程房间与广播；
        同时 _sid_user_map 也应迁移到共享存储（Redis）以免身份态不一致。
"""

import datetime
import logging
import threading

import jwt
from django.conf import settings

from gvsdsdk.auth.middleware import ResolveRoleContext
from gvsdsdk.model_utils import UUIDObj
from gvsdsdk.msync.sio import sio

logger = logging.getLogger(__name__)

#: 用户级命名空间
TALK_NS = '/talk'

#: 用户令牌有效期（小时）
TALK_TOKEN_TTL_HOURS = 12

#: JWT 算法（HS256，密钥为 settings.SECRET_KEY）
TALK_TOKEN_ALGORITHM = 'HS256'

#: 与 gvsds/auth.py 保持一致的 Cookie 名（此处不直接 import gvsds.auth，
#: 以避免 gvsdsdk 对 gvsds 业务包的强依赖；如需改名请同步两处）
JWT_ACCESS_COOKIE = 'AccessToken'

#: sid → 用户上下文字典（进程内，单进程限制见模块 docstring）
_sid_user_map = {}
_map_lock = threading.Lock()


# ── 基础助手（与 gvsdsdk.msync.sio 的响应约定保持一致） ──

def _ok(data=None, msg='success'):
    return {'code': 200, 'msg': msg, 'data': data}


def _err(msg, code=400):
    return {'code': code, 'msg': msg, 'data': None}


def _d(data, key, default=None):
    """安全获取字典字段"""
    if not isinstance(data, dict):
        return default
    return data.get(key, default)


def _UuidBytes(value):
    """任意 UUID 表示（str/bytes/uuid.UUID/UUIDObj）→ 16 字节；非法或空返回 None"""
    if value is None or value == '':
        return None
    try:
        return UUIDObj(value).bytes
    except (ValueError, TypeError, AttributeError):
        return None


def _UuidStr(value):
    """任意 UUID 表示 → 字符串；非法或空返回 None"""
    if value is None or value == '':
        return None
    try:
        return UUIDObj(value).str
    except (ValueError, TypeError, AttributeError):
        return None


def _ConvRoom(ConversationUUID):
    """会话房间名（字符串形式，str/bytes 入参均可处理）；非法返回 None"""
    conversation_str = _UuidStr(ConversationUUID)
    if not conversation_str:
        return None
    return f'conv:{conversation_str}'


def _ParseCookies(RawCookie):
    """解析 WSGI environ['HTTP_COOKIE'] 为 {name: value}"""
    cookies = {}
    if not RawCookie:
        return cookies
    from http.cookies import SimpleCookie
    jar = SimpleCookie()
    try:
        jar.load(RawCookie)
    except Exception:
        logger.exception('[TALK] Cookie 解析失败')
        return cookies
    for key, morsel in jar.items():
        cookies[key] = morsel.value
    return cookies


class _RequestShim:
    """轻量请求 shim——让 ResolveRoleContext 可在 WSGI environ 场景复用

    仅提供 ResolveRoleContext 所需的属性：COOKIES / user，
    以及会被其原地赋值的 current_role / current_company_uuid / current_tenant_uuid。
    """

    def __init__(self, Cookies, User):
        self.COOKIES = Cookies or {}
        self.user = User
        self.current_role = None
        self.current_company_uuid = None
        self.current_tenant_uuid = None


# ── 令牌签发 / 校验 ──

def IssueUserToken(UserUUID, CompanyUUID, TenantUUID, RoleUUID):
    """签发用户级令牌

    使用 settings.SECRET_KEY 作为 HMAC 密钥，算法 HS256（复用 simplejwt 已依赖的 PyJWT，
    不新增第三方依赖）。令牌内 UUID 以字符串形式承载（JSON 安全），校验后统一转为字节。
    有效期 TALK_TOKEN_TTL_HOURS（12）小时。

    Args:
        UserUUID:     用户 UUID（str/bytes/uuid.UUID/UUIDObj）
        CompanyUUID:  当前公司 UUID，可为 None
        TenantUUID:   当前租户 UUID，可为 None
        RoleUUID:     当前角色 UUID，可为 None

    Returns:
        str: 编码后的 JWT
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        'UserUUID': _UuidStr(UserUUID),
        'CompanyUUID': _UuidStr(CompanyUUID),
        'TenantUUID': _UuidStr(TenantUUID),
        'RoleUUID': _UuidStr(RoleUUID),
        'iat': now,
        'exp': now + datetime.timedelta(hours=TALK_TOKEN_TTL_HOURS),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=TALK_TOKEN_ALGORITHM)


def VerifyUserToken(Token):
    """校验用户级令牌

    Args:
        Token: JWT 字符串

    Returns:
        dict | None: 合法时返回 {'UserUUID','CompanyUUID','TenantUUID','RoleUUID'}（值均为 16 字节
        或 None）；令牌缺失/非法/过期，或缺少 UserUUID 时返回 None。
    """
    if not Token:
        return None
    try:
        claims = jwt.decode(Token, settings.SECRET_KEY, algorithms=[TALK_TOKEN_ALGORITHM])
    except jwt.InvalidTokenError:
        # InvalidTokenError 是 ExpiredSignatureError 等所有解码异常的基类
        return None
    user_uuid = _UuidBytes(claims.get('UserUUID'))
    if not user_uuid:
        return None
    return {
        'UserUUID': user_uuid,
        'CompanyUUID': _UuidBytes(claims.get('CompanyUUID')),
        'TenantUUID': _UuidBytes(claims.get('TenantUUID')),
        'RoleUUID': _UuidBytes(claims.get('RoleUUID')),
    }


def ResolveUserFromEnviron(Environ):
    """从 WSGI environ 解析当前用户上下文

    流程：environ['HTTP_COOKIE'] → AccessToken → VerifyUserToken 得到用户身份；
    再用 CurrentRoleUUID 经 ResolveRoleContext（唯一角色解析实现）解析当前
    公司/租户/角色，并以解析结果为准（解析不到时回退到令牌内的声明）。

    Args:
        Environ: WSGI environ 字典

    Returns:
        dict | None: 认证成功返回
        {'UserUUID','CompanyUUID','TenantUUID','RoleUUID'}（值均为 16 字节或 None）；
        未认证返回 None。
    """
    try:
        cookies = _ParseCookies((Environ or {}).get('HTTP_COOKIE', ''))
        claims = VerifyUserToken(cookies.get(JWT_ACCESS_COOKIE, ''))
        if claims is None:
            return None

        user_uuid = claims['UserUUID']
        user = _GetUser(user_uuid)

        # 复用唯一角色解析实现（内部为同步 ORM；Main.py 已 monkey.patch_all()，
        # gevent 下会正确让出协程，无需额外处理）
        shim = _RequestShim(cookies, user)
        ResolveRoleContext(shim, user)

        company_uuid = _UuidBytes(shim.current_company_uuid) or claims.get('CompanyUUID')
        tenant_uuid = _UuidBytes(shim.current_tenant_uuid) or claims.get('TenantUUID')
        role_uuid = _UuidBytes(getattr(shim.current_role, 'RoleUUID', None)) or claims.get('RoleUUID')

        return {
            'UserUUID': user_uuid,
            'CompanyUUID': company_uuid,
            'TenantUUID': tenant_uuid,
            'RoleUUID': role_uuid,
        }
    except Exception:
        logger.exception('[TALK] ResolveUserFromEnviron 解析失败')
        return None


def _GetUser(UserUUID):
    """按 UserUUID（16 字节）取用户实例；取不到返回 None"""
    try:
        from gvsds.apps.core.models import User
    except ImportError:
        logger.warning('[TALK] gvsds.apps.core.models.User 不可用')
        return None
    return User.objects.filter(UserUUID=UserUUID).first()


# ── 广播 ──

def BroadcastToConversation(ConversationUUID, Event, Data, SkipSid=None):
    """向会话房间广播事件

    房间名固定为 f'conv:{ConversationUUID}'（ConversationUUID 传 str/bytes 均可）。

    Args:
        ConversationUUID: 会话 UUID
        Event: 事件名
        Data: 事件负载
        SkipSid: 可选，跳过该 sid（如输入中事件不回发给发起者）

    注意：单进程限制——只能触达本进程内的连接（见模块 docstring）。
    """
    room = _ConvRoom(ConversationUUID)
    if not room:
        logger.warning('[TALK] BroadcastToConversation 收到非法 ConversationUUID: %r', ConversationUUID)
        return
    sio.emit(Event, Data, namespace=TALK_NS, room=room, skip_sid=SkipSid)


def BroadcastToUser(UserUUID, Event, Data):
    """向某用户的全部连接推送（用于未读角标等）

    注意：单进程限制——仅本进程内该用户的连接可见（见模块 docstring）。
    """
    target = _UuidBytes(UserUUID)
    if not target:
        logger.warning('[TALK] BroadcastToUser 收到非法 UserUUID: %r', UserUUID)
        return
    with _map_lock:
        sids = [sid for sid, ctx in _sid_user_map.items() if ctx.get('UserUUID') == target]
    for sid in sids:
        try:
            sio.emit(Event, Data, namespace=TALK_NS, to=sid)
        except Exception:
            logger.exception('[TALK] BroadcastToUser 推送失败 sid=%s', sid)


# ── sid 身份态助手 ──

def _SetSidUser(sid, Context):
    with _map_lock:
        _sid_user_map[sid] = Context


def _GetSidUser(sid):
    with _map_lock:
        return _sid_user_map.get(sid)


def _PopSidUser(sid):
    with _map_lock:
        return _sid_user_map.pop(sid, None)


def _ReadyPayload(Context):
    return _ok({
        'UserUUID': _UuidStr(Context.get('UserUUID')),
        'CompanyUUID': _UuidStr(Context.get('CompanyUUID')),
    })


def _Fail(sid, Error, Code=500):
    """统一异常处理：记录堆栈、向该 sid 推 talk:error、返回 _err"""
    logger.exception('[TALK] 处理失败 sid=%s code=%s', sid, Code)
    payload = _err(str(Error), Code)
    try:
        sio.emit('talk:error', payload, namespace=TALK_NS, to=sid)
    except Exception:
        logger.exception('[TALK] 推送 talk:error 失败 sid=%s', sid)
    return payload


def _JoinUserConversations(sid, Context):
    """把 sid 加入该用户所在的所有会话房间

    会话列表由 gvsds 侧 Talk 模块提供（gvsds.apps.talk.manager.ListConversationUUIDsForUser）。
    该模块可能尚未启用，故惰性导入并容错：导入失败时仅绑定 sid→user，不加入任何房间。
    """
    try:
        from gvsds.apps.talk.manager import ListConversationUUIDsForUser
    except ImportError:
        logger.warning('[TALK] gvsds.apps.talk.manager 不可用，跳过会话房间加入')
        return
    try:
        try:
            conversation_uuids = ListConversationUUIDsForUser(
                Context.get('UserUUID'), Context.get('CompanyUUID'),
            )
        except TypeError:
            # 兼容仅接收 UserUUID 的实现
            conversation_uuids = ListConversationUUIDsForUser(Context.get('UserUUID'))
    except Exception:
        logger.exception('[TALK] 获取用户会话列表失败 sid=%s', sid)
        return
    for conversation_uuid in (conversation_uuids or []):
        room = _ConvRoom(conversation_uuid)
        if not room:
            continue
        try:
            sio.enter_room(sid, room, namespace=TALK_NS)
        except Exception:
            logger.exception('[TALK] 加入会话房间失败 sid=%s room=%s', sid, room)


# ── 事件处理器 ──

@sio.on('connect', namespace=TALK_NS)
def on_talk_connect(sid, environ, auth=None):
    """连接握手：尝试从 Cookie 或 auth.Token 认证

    认证失败“不拒绝连接”（否则前端只看到握手失败、拿不到原因），
    仅记录未认证状态并等待 talk:auth；所有业务事件都会再次校验认证态。
    """
    try:
        context = ResolveUserFromEnviron(environ)
        if context is None:
            claims = VerifyUserToken(_d(auth, 'Token', ''))
            if claims:
                context = dict(claims)

        if not context:
            logger.warning('[TALK] connect 未认证 sid=%s，等待 talk:auth', sid)
            return

        _SetSidUser(sid, context)
        _JoinUserConversations(sid, context)
        sio.emit('talk:ready', _ReadyPayload(context), namespace=TALK_NS, to=sid)
        logger.info('[TALK] connect 已认证 sid=%s user=%s', sid, _UuidStr(context.get('UserUUID')))
    except Exception as e:
        _Fail(sid, e)


@sio.on('disconnect', namespace=TALK_NS)
def on_talk_disconnect(sid, reason=None):
    """断开：清理身份态并离开所有房间"""
    try:
        _PopSidUser(sid)
        try:
            rooms = sio.rooms(sid, namespace=TALK_NS) or []
        except Exception:
            rooms = []
        for room in rooms:
            if room == sid:
                continue
            try:
                sio.leave_room(sid, room, namespace=TALK_NS)
            except Exception:
                logger.exception('[TALK] 离开房间失败 sid=%s room=%s', sid, room)
        logger.info('[TALK] disconnect sid=%s reason=%s', sid, reason)
    except Exception:
        logger.exception('[TALK] disconnect 清理失败 sid=%s', sid)


@sio.on('talk:auth', namespace=TALK_NS)
def on_talk_auth(sid, data):
    """用户令牌认证（Cookie 不可用时的兜底），成功后绑定 sid→user 并加入会话房间"""
    try:
        claims = VerifyUserToken(_d(data, 'Token', ''))
        if not claims:
            return _err('认证失败', 401)

        _SetSidUser(sid, claims)
        _JoinUserConversations(sid, claims)
        payload = _ReadyPayload(claims)
        sio.emit('talk:ready', payload, namespace=TALK_NS, to=sid)
        return payload
    except Exception as e:
        return _Fail(sid, e)


@sio.on('talk:send', namespace=TALK_NS)
def on_talk_send(sid, data):
    """发送消息：落库后向会话房间广播 talk:message"""
    try:
        context = _GetSidUser(sid)
        if not context or not context.get('UserUUID'):
            return _err('未认证', 401)

        conversation_uuid = _UuidBytes(_d(data, 'ConversationUUID'))
        if not conversation_uuid:
            return _err('缺少 ConversationUUID', 400)

        try:
            from gvsds.apps.talk.manager import SendMessageByUser
        except ImportError:
            return _err('Talk 服务未启用', 501)

        try:
            message = SendMessageByUser(
                context['UserUUID'],
                context.get('CompanyUUID'),
                conversation_uuid,
                MsgType=_d(data, 'MsgType', 0),
                Content=_d(data, 'Content', ''),
                AttachmentSHA1=_d(data, 'AttachmentSHA1', ''),
                AttachmentName=_d(data, 'AttachmentName', ''),
                AttachmentSize=_d(data, 'AttachmentSize', 0),
                AttachmentMime=_d(data, 'AttachmentMime', ''),
                SkipSid=sid,
            )
        except Exception as e:
            # 业务异常：向发起者单独推送 talk:error
            return _Fail(sid, e)

        # 注意：talk:message 广播已由 TalkManager.SendMessage 内部完成
        # （REST 与 socket 共用同一广播出口），此处不得重复广播
        return _ok(message)
    except Exception as e:
        return _Fail(sid, e)


@sio.on('talk:read', namespace=TALK_NS)
def on_talk_read(sid, data):
    """标记已读：落库后向会话房间广播 talk:read"""
    try:
        context = _GetSidUser(sid)
        if not context or not context.get('UserUUID'):
            return _err('未认证', 401)

        conversation_uuid = _UuidBytes(_d(data, 'ConversationUUID'))
        if not conversation_uuid:
            return _err('缺少 ConversationUUID', 400)
        last_read_uuid = _UuidBytes(_d(data, 'LastReadMessageUUID'))

        try:
            from gvsds.apps.gerp.apps.talk.manager import MarkReadByUser
        except ImportError:
            return _err('Talk 服务未启用', 501)

        try:
            success = MarkReadByUser(context['UserUUID'], conversation_uuid, last_read_uuid)
        except Exception as e:
            return _Fail(sid, e)
        if not success:
            return _err('标记已读失败', 400)

        # 注意：talk:read 广播已由 TalkManager.MarkRead 内部完成（BroadcastRead），
        # 此处不得重复广播
        return _ok(msg='已标记已读')
    except Exception as e:
        return _Fail(sid, e)


@sio.on('talk:typing', namespace=TALK_NS)
def on_talk_typing(sid, data):
    """正在输入：向会话房间广播（跳过发起者自身）"""
    try:
        context = _GetSidUser(sid)
        if not context or not context.get('UserUUID'):
            return _err('未认证', 401)

        conversation_uuid = _UuidBytes(_d(data, 'ConversationUUID'))
        if not conversation_uuid:
            return _err('缺少 ConversationUUID', 400)

        BroadcastToConversation(conversation_uuid, 'talk:typing', {
            'ConversationUUID': _UuidStr(conversation_uuid),
            'UserUUID': _UuidStr(context['UserUUID']),
        }, SkipSid=sid)
        return _ok()
    except Exception as e:
        return _Fail(sid, e)


@sio.on('talk:ping', namespace=TALK_NS)
def on_talk_ping(sid, data=None):
    """应用层心跳"""
    try:
        payload = _ok()
        sio.emit('talk:pong', payload, namespace=TALK_NS, to=sid)
        return payload
    except Exception as e:
        return _Fail(sid, e)

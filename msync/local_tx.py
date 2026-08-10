"""本地事务管理器

每个工作服务运行一个 LocalTransactionManager 实例，
负责注册本服务的步骤处理器（action + compensate），
并在收到主服务器请求时执行本地逻辑。

用法（在服务启动时注册处理器）：
    from gvsdsdk.msync.local_tx import local_tx

    local_tx.register(
        'create_user',
        action=create_user_action,
        compensate=create_user_compensate,
    )
"""

import logging
import time

logger = logging.getLogger(__name__)


class StepHandler:
    """步骤处理器——封装 action 与 compensate 函数"""

    def __init__(self, name, action, compensate=None):
        self.Name = name
        self.Action = action
        self.Compensate = compensate

    def __repr__(self):
        return f'<StepHandler:{self.Name}>'


class LocalTransactionManager:
    """本地事务管理器——每个工作服务一个实例"""

    #: 幂等缓存 TTL（秒）：主服务器崩溃窗口内重复发送同一步骤时去重
    IDEMPOTENCY_TTL_SECONDS = 300

    def __init__(self):
        self._handlers = {}
        self._idempotency_cache = {}

    def register(self, name, action, compensate=None):
        """注册步骤处理器"""
        if name in self._handlers:
            logger.warning(f'步骤处理器已存在，覆盖: {name}')
        self._handlers[name] = StepHandler(
            name=name,
            action=action,
            compensate=compensate,
        )
        logger.info(f'步骤处理器注册: {name}')

    def unregister(self, name):
        """注销步骤处理器"""
        if name in self._handlers:
            del self._handlers[name]
            logger.info(f'步骤处理器注销: {name}')

    def HasHandler(self, name):
        """检查是否注册了指定步骤处理器"""
        return name in self._handlers

    def ListHandlers(self):
        """列出所有已注册的步骤处理器名称"""
        return list(self._handlers.keys())

    # ─── 幂等去重（可选能力，收到 Idempotency-Key 时启用）────────────

    def _IdempotencyGet(self, key):
        if not key:
            return None
        entry = self._idempotency_cache.get(key)
        if not entry:
            return None
        if time.time() - entry['ts'] > self.IDEMPOTENCY_TTL_SECONDS:
            self._idempotency_cache.pop(key, None)
            return None
        return entry['result']

    def _IdempotencySet(self, key, result):
        if not key:
            return
        # 惰性清理：条目数超限时剔除过期项，防止缓存无限增长
        if len(self._idempotency_cache) >= 1000:
            now = time.time()
            expired = [
                k for k, v in self._idempotency_cache.items()
                if now - v['ts'] > self.IDEMPOTENCY_TTL_SECONDS
            ]
            for k in expired:
                self._idempotency_cache.pop(k, None)
        self._idempotency_cache[key] = {'ts': time.time(), 'result': result}

    def ExecuteStep(self, name, payload=None, idempotency_key=None):
        """执行步骤——调用本地 action 函数

        Args:
            name: 步骤处理器名称
            payload: 载荷
            idempotency_key: 幂等键（主服务器 Idempotency-Key 头），
                相同键在 TTL 内重复调用直接返回首次结果
        """
        cached = self._IdempotencyGet(idempotency_key)
        if cached is not None:
            logger.info(f'步骤执行幂等命中: {name} key={idempotency_key}')
            return cached

        handler = self._handlers.get(name)
        if handler is None:
            msg = f'未注册的步骤处理器: {name}'
            logger.error(msg)
            result = {'success': False, 'result': None, 'error': msg}
            self._IdempotencySet(idempotency_key, result)
            return result

        try:
            result = handler.Action(payload or {})
            logger.info(f'步骤执行成功: {name}')
            result = {'success': True, 'result': result, 'error': None}
        except Exception as e:
            logger.error('步骤执行失败: %s: %s', name, e, exc_info=True)
            result = {'success': False, 'result': None, 'error': str(e)}

        self._IdempotencySet(idempotency_key, result)
        return result

    def CompensateStep(self, name, payload=None, action_result=None, idempotency_key=None):
        """补偿步骤——调用本地 compensate 函数

        Args:
            name: 步骤处理器名称
            payload: 载荷
            action_result: 原始执行结果（幂等回滚依据）
            idempotency_key: 幂等键（主服务器 Idempotency-Key 头）
        """
        cached = self._IdempotencyGet(idempotency_key)
        if cached is not None:
            logger.info(f'步骤补偿幂等命中: {name} key={idempotency_key}')
            return cached

        handler = self._handlers.get(name)
        if handler is None:
            msg = f'未注册的步骤处理器: {name}'
            logger.error(msg)
            result = {'success': False, 'result': None, 'error': msg}
            self._IdempotencySet(idempotency_key, result)
            return result

        if handler.Compensate is None:
            logger.warning(f'步骤无补偿函数，跳过: {name}')
            result = {'success': True, 'result': None, 'error': None}
            self._IdempotencySet(idempotency_key, result)
            return result

        try:
            result = handler.Compensate(payload or {}, action_result or {})
            logger.info(f'步骤补偿成功: {name}')
            result = {'success': True, 'result': result, 'error': None}
        except Exception as e:
            logger.error('步骤补偿失败: %s: %s', name, e, exc_info=True)
            result = {'success': False, 'result': None, 'error': str(e)}

        self._IdempotencySet(idempotency_key, result)
        return result


local_tx = LocalTransactionManager()

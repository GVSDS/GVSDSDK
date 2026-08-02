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

    def __init__(self):
        self._handlers = {}

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

    def ExecuteStep(self, name, payload=None):
        """执行步骤——调用本地 action 函数"""
        handler = self._handlers.get(name)
        if handler is None:
            msg = f'未注册的步骤处理器: {name}'
            logger.error(msg)
            return {'success': False, 'result': None, 'error': msg}

        try:
            result = handler.Action(payload or {})
            logger.info(f'步骤执行成功: {name}')
            return {'success': True, 'result': result, 'error': None}
        except Exception as e:
            logger.error('步骤执行失败: %s: %s', name, e, exc_info=True)
            return {'success': False, 'result': None, 'error': str(e)}

    def CompensateStep(self, name, payload=None, action_result=None):
        """补偿步骤——调用本地 compensate 函数"""
        handler = self._handlers.get(name)
        if handler is None:
            msg = f'未注册的步骤处理器: {name}'
            logger.error(msg)
            return {'success': False, 'result': None, 'error': msg}

        if handler.Compensate is None:
            logger.warning(f'步骤无补偿函数，跳过: {name}')
            return {'success': True, 'result': None, 'error': None}

        try:
            result = handler.Compensate(payload or {}, action_result or {})
            logger.info(f'步骤补偿成功: {name}')
            return {'success': True, 'result': result, 'error': None}
        except Exception as e:
            logger.error('步骤补偿失败: %s: %s', name, e, exc_info=True)
            return {'success': False, 'result': None, 'error': str(e)}


local_tx = LocalTransactionManager()

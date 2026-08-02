"""MSYNC 分布式事务模块

薄封装：直接转发到 gvsds.apps.msync.transaction 原生层。
"""

try:
    from gvsds.apps.msync.transaction import TransactionManager, TransactionError, StepExecutionError
except ImportError:
    raise ImportError(
        'TransactionManager 仅在主服务器环境可用。'
        '子服务器请通过 LocalTransactionManager 注册本地步骤处理器。'
    )

__all__ = [
    'TransactionManager',
    'TransactionError',
    'StepExecutionError',
]

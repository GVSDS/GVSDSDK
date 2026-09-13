"""模块：Celery 任务工具装饰器

提供事务回滚、重试、日志、安全 Decimal 运算等通用装饰器，
统一异步任务的异常处理与可观测性。

依赖 Celery（gvsds/celery.py），无 Celery 时装饰器自动降级为同步执行。

常用 API：
    from gvsdsdk.celery_utils import RollbackOnFailure, RetryOnFailure, LogTask, SafeDecimal

    @App.task
    @RollbackOnFailure
    @RetryOnFailure(max_retries=3, delay=5)
    @LogTask
    def CriticalTask(pk):
        ...

    @SafeDecimal(default=Decimal('0'))
    def AggregateStats(qs):
        ...
"""
import logging
import functools
from decimal import Decimal

logger = logging.getLogger(__name__)

try:
    from celery import shared_task
    _HAS_CELERY = True
except ImportError:
    _HAS_CELERY = False
    shared_task = None


def _IsCeleryTask(func):
    """判断函数是否为 Celery 任务对象。"""
    return _HAS_CELERY and hasattr(func, 'apply_async')


def RollbackOnFailure(func):
    """事务回滚装饰器：任务异常时自动回滚当前事务。

    适用于写操作任务，防止部分写入导致数据不一致。
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception:
            try:
                from django.db import transaction
                transaction.set_rollback(True)
                logger.warning('[CeleryUtils] 任务异常，已标记事务回滚: %s', func.__name__, exc_info=True)
            except Exception:
                pass
            raise
    return wrapper


def RetryOnFailure(max_retries=3, delay=5, exceptions=(Exception,)):
    """重试装饰器：任务异常时自动重试。

    参数：
        max_retries  最大重试次数
        delay        重试间隔秒
        exceptions   触发重试的异常类型
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt < max_retries:
                        logger.info('[CeleryUtils] 任务重试 %d/%d: %s', attempt + 1, max_retries, func.__name__)
                        import time
                        time.sleep(delay)
                    else:
                        logger.error('[CeleryUtils] 任务重试耗尽: %s', func.__name__, exc_info=True)
            raise last_exc
        return wrapper
    return decorator


def LogTask(func):
    """日志装饰器：记录任务开始/成功/失败。"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        name = func.__name__
        logger.info('[CeleryUtils] 任务开始: %s', name)
        try:
            result = func(*args, **kwargs)
            logger.info('[CeleryUtils] 任务成功: %s', name)
            return result
        except Exception:
            logger.error('[CeleryUtils] 任务失败: %s', name, exc_info=True)
            raise
    return wrapper


def SafeDecimal(default=Decimal('0')):
    """安全 Decimal 运算装饰器：异常时返回 default。

    适用于聚合统计任务，单条脏数据不中断整体计算。
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except (ArithmeticError, TypeError, ValueError):
                logger.warning('[CeleryUtils] Decimal 运算异常，返回默认值: %s', func.__name__, exc_info=True)
                return default
        return wrapper
    return decorator

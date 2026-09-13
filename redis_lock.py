"""模块：Redis 分布式锁

基于 Django cache 框架实现，底层使用 Redis SET NX EX 原子操作。
提供上下文管理器与装饰器两种用法，跨进程互斥。

依赖：settings.CACHES 配置 redis 后端（见 settings.py）。

常用 API：
    from gvsdsdk.redis_lock import RedisLock, redis_locked

    # 上下文管理器
    with RedisLock('order:pay:123', timeout=30) as acquired:
        if not acquired:
            raise RuntimeError('订单正在处理中，请勿重复提交')
        DoPay(order)

    # 装饰器
    @redis_locked('resource_key', timeout=60, wait=True)
    def CriticalTask(pk):
        ...

若未配置 Redis 缓存，自动降级为 no-op（acquired 永远 True），便于本地开发。
"""
import logging
import time
import uuid
from functools import wraps

from django.core.cache import cache

logger = logging.getLogger(__name__)

_LOCK_PREFIX = 'gvsds:lock:'


def _is_redis_backend():
    """探测当前 cache 是否为 redis 后端。"""
    try:
        backend = cache.__class__.__module__ + '.' + cache.__class__.__name__
        return 'redis' in backend.lower()
    except Exception:
        return False


_REDIS_OK = None


def _HasRedis():
    """惰性探测 Redis 是否可用（结果缓存）。"""
    global _REDIS_OK
    if _REDIS_OK is None:
        _REDIS_OK = _is_redis_backend()
    return _REDIS_OK


class RedisLock:
    """分布式锁（上下文管理器）。

    参数：
        key      锁名（自动加前缀避免冲突）
        timeout  锁超时秒数（防死锁，到期自动释放）
        wait     未获取时是否阻塞等待
        retry    阻塞时重试间隔秒
        retry_count 阻塞时最大重试次数（0=不重试）
    """

    def __init__(self, key, timeout=30, wait=False, retry=0.1, retry_count=100):
        self.full_key = _LOCK_PREFIX + key
        self.timeout = timeout
        self.wait = wait
        self.retry = retry
        self.retry_count = retry_count
        self.token = uuid.uuid4().hex
        self._acquired = False

    def acquire(self):
        """尝试获取锁，返回是否成功。"""
        if not _HasRedis():
            # 无 Redis 时降级为 no-op
            self._acquired = True
            return True
        # add 是原子的：仅当 key 不存在时设置
        got = cache.add(self.full_key, self.token, timeout=self.timeout)
        if got:
            self._acquired = True
            return True
        if not self.wait:
            return False
        for _ in range(self.retry_count):
            time.sleep(self.retry)
            got = cache.add(self.full_key, self.token, timeout=self.timeout)
            if got:
                self._acquired = True
                return True
        return False

    def release(self):
        """释放锁。仅当 token 匹配才删除，防止误释放他人持有的锁。"""
        if not self._acquired or not _HasRedis():
            return
        try:
            current = cache.get(self.full_key)
            if current == self.token:
                cache.delete(self.full_key)
        except Exception:
            logger.warning('[RedisLock] 释放锁失败: %s', self.full_key, exc_info=True)
        finally:
            self._acquired = False

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
        return False

    @property
    def acquired(self):
        return self._acquired


def redis_locked(key, timeout=30, wait=False, retry=0.1, retry_count=100, key_arg=None):
    """装饰器形式分布式锁。

    参数：
        key      静态锁名
        key_arg  动态锁名取自被装饰函数的参数索引或参数名
                 如 key_arg=0 取第一个位置参数，key_arg='pk' 取名为 pk 的关键字参数
        timeout  锁超时秒
        wait     未获取时是否阻塞等待

    用法：
        @redis_locked('order:pay', timeout=30, key_arg='order_uuid')
        def PayOrder(order_uuid):
            ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if key_arg is not None:
                if isinstance(key_arg, int) and key_arg < len(args):
                    lock_key = f'{key}:{args[key_arg]}'
                elif key_arg in kwargs:
                    lock_key = f'{key}:{kwargs[key_arg]}'
                else:
                    lock_key = key
            else:
                lock_key = key
            with RedisLock(lock_key, timeout=timeout, wait=wait, retry=retry, retry_count=retry_count) as got:
                if not got:
                    raise RuntimeError(f'资源 {lock_key} 正被占用，请稍后重试')
                return func(*args, **kwargs)
        return wrapper
    return decorator

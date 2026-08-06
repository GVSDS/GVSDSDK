"""gvsdsdk.stats — 统计引擎

子服务器通过 StatsManager 直接调用统计能力：
原子增量、周期查询、按维度聚合、排行榜生成、周期重置、过期清理。
"""

__all__ = [
    'StatsManager',
    'StatMetric', 'StatDimension', 'StatRecord', 'StatRanking', 'StatConfig',
]


def __getattr__(name):
    if name == 'StatsManager':
        from gvsdsdk.stats import manager as _m
        val = getattr(_m, name)
        globals()[name] = val
        return val
    if name in ('StatMetric', 'StatDimension', 'StatRecord', 'StatRanking', 'StatConfig'):
        from gvsdsdk.stats import models as _m
        val = getattr(_m, name)
        globals()[name] = val
        return val
    raise AttributeError(f"module 'gvsdsdk.stats' has no attribute {name!r}")

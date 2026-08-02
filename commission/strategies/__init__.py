"""gvsdsdk.commission.strategies — 分佣策略引擎"""


def __getattr__(name):
    if name in ('CreateStrategy', 'BaseSplitStrategy', 'PercentageStrategy', 'FixedStrategy', 'TieredStrategy', 'RemainderStrategy', 'WithdrawalFeeStrategy'):
        from gvsdsdk.commission.strategies import engine as _m
        val = getattr(_m, name)
        globals()[name] = val
        return val
    raise AttributeError(f"module 'gvsdsdk.commission.strategies' has no attribute {name!r}")

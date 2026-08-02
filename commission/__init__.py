"""gvsdsdk.commission — 分佣引擎"""

__all__ = [
    'CommissionEvent', 'RateTable', 'RateEntry', 'CommissionRule',
    'CommissionRecord', 'PreSettlement',
    'CommissionEngine', 'CommissionError',
    'CreateStrategy', 'BaseSplitStrategy', 'PercentageStrategy',
    'FixedStrategy', 'TieredStrategy', 'RemainderStrategy', 'WithdrawalFeeStrategy',
]


def __getattr__(name):
    if name in ('CommissionEvent', 'RateTable', 'RateEntry', 'CommissionRule', 'CommissionRecord', 'PreSettlement'):
        from gvsdsdk.commission import models as _m
        val = getattr(_m, name)
        globals()[name] = val
        return val
    if name in ('CommissionEngine', 'CommissionError'):
        from gvsdsdk.commission import manager as _m
        val = getattr(_m, name)
        globals()[name] = val
        return val
    if name in ('CreateStrategy', 'BaseSplitStrategy', 'PercentageStrategy', 'FixedStrategy', 'TieredStrategy', 'RemainderStrategy', 'WithdrawalFeeStrategy'):
        from gvsdsdk.commission.strategies import engine as _m
        val = getattr(_m, name)
        globals()[name] = val
        return val
    raise AttributeError(f"module 'gvsdsdk.commission' has no attribute {name!r}")

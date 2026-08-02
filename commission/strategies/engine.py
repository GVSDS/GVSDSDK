"""模块：分佣策略引擎
策略模式实现——每种分账方式独立封装，新增分账逻辑只需添加新策略类。

支持四种策略：
  PercentageStrategy: 比例分账  → TotalAmount × rate
  FixedStrategy:      固定金额   → 固定值（受保底/封顶约束）
  TieredStrategy:     阶梯分账   → 按次数/档位查表
  RemainderStrategy:  剩余归平台 → TotalAmount - SUM(其他参与者)
"""

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Dict, Optional
from gvsdsdk.commission.models import RateTable


class BaseSplitStrategy(ABC):
    """分账策略抽象基类"""

    def __init__(self, participant_rule: dict, rate_table: Optional[RateTable] = None):
        self._rule = participant_rule
        self._rate_table = rate_table

    @abstractmethod
    def Calculate(self, total_amount: Decimal, tier_index: int = 0, **context) -> Decimal:
        ...

    @property
    def Role(self):
        return self._rule.get('Role') or self._rule.get('role', 'unknown')

    @property
    def SplitType(self):
        return self._rule.get('Type') or self._rule.get('type', 'unknown')

    def _get_rate_from_table(self, key):
        if self._rate_table:
            rate = self._rate_table.GetRate(key)
            if rate is not None:
                return rate
        return self._rule.get('Rate', Decimal('0'))


class PercentageStrategy(BaseSplitStrategy):
    """比例分账策略

    配置示例:
        { Role: 'worker', Type: 'percentage', Value: 0.80 }
        { Role: 'worker', Type: 'percentage', RateTableKey: '1' }
    """

    def Calculate(self, total_amount: Decimal, tier_index: int = 0, **context) -> Decimal:
        rate_key = self._rule.get('RateTableKey', '')
        if rate_key:
            rate = self._get_rate_from_table(rate_key)
        else:
            rate = Decimal(str(self._rule.get('Value', 0)))
        if not 0 <= rate <= 1:
            raise ValueError(f'PercentageStrategy rate 必须在 [0,1] 之间, got {rate}')
        amount = total_amount * rate
        return self._apply_bounds(amount, total_amount)

    def _apply_bounds(self, amount, total_amount):
        minimum = self._rule.get('MinAmount')
        maximum = self._rule.get('MaxAmount')
        if minimum is not None:
            amount = max(amount, Decimal(str(minimum)))
        if maximum is not None:
            amount = min(amount, Decimal(str(maximum)))
        return amount


class FixedStrategy(BaseSplitStrategy):
    """固定金额策略

    配置示例:
        { Role: 'agent', Type: 'fixed', Value: 10 }
        { Role: 'leader', Type: 'fixed', RateTableKey: 'guanshi_fc' }
    """

    def Calculate(self, total_amount: Decimal, tier_index: int = 0, **context) -> Decimal:
        rate_key = self._rule.get('RateTableKey', '')
        if rate_key:
            amount = self._get_rate_from_table(rate_key)
        else:
            amount = Decimal(str(self._rule.get('Value', 0)))
        return amount


class TieredStrategy(BaseSplitStrategy):
    """阶梯分账策略——按次数/档位查表

    配置示例:
        { Role: 'agent', Type: 'tiered',
          Tiers: { 1: 5, 2: 8, 3: 10 } }
    或从费率表中按 key_N 形式查找。
    """

    def Calculate(self, total_amount: Decimal, tier_index: int = 0, **context) -> Decimal:
        tiers = self._rule.get('Tiers', {})
        if tiers:
            # 兼容整型键和字符串键（JSON 配置可能用任意类型）
            raw_value = tiers.get(tier_index)
            if raw_value is None:
                raw_value = tiers.get(str(tier_index), tiers.get('default', 0))
            if isinstance(raw_value, str) and raw_value.endswith('%'):
                return total_amount * Decimal(raw_value.rstrip('%')) / Decimal('100')
            return Decimal(str(raw_value))

        tier_key = self._rule.get('TierKeyPrefix', '')
        if tier_key and self._rate_table:
            rate = self._rate_table.GetRate(f'{tier_key}_{tier_index}')
            if rate is not None:
                return rate

        return Decimal('0')


class RemainderStrategy(BaseSplitStrategy):
    """剩余归平台策略——扣除所有其他参与者后的余额

    配置示例:
        { Role: 'platform', Type: 'remainder' }
    """

    def Calculate(self, total_amount: Decimal, tier_index: int = 0, **context) -> Decimal:
        allocated = context.get('AllocatedAmount', Decimal('0'))
        remainder = total_amount - allocated
        return remainder if remainder > 0 else Decimal('0')


class WithdrawalFeeStrategy(BaseSplitStrategy):
    """提现手续费策略

    配置示例:
        { Role: 'platform', Type: 'withdrawal_fee', Value: 0.01 }
    """

    def Calculate(self, total_amount: Decimal, tier_index: int = 0, **context) -> Decimal:
        rate_key = self._rule.get('RateTableKey', '')
        if rate_key:
            rate = self._get_rate_from_table(rate_key)
        else:
            rate = Decimal(str(self._rule.get('Value', 0)))
        return total_amount * rate


# 注意：'withdrawal_fee' 用于提现手续费场景，属于独立扩展策略，
# 未列入 CommissionRule.SPLT_TYPE_CHOICES（该 choices 仅含 percentage/fixed/tiered/remainder）；
# 若需通过 CommissionRule.SplitType 配置提现手续费，应在 commission/models.py 的 SPLT_TYPE_CHOICES 中补充。
STRATEGY_MAP: Dict[str, type] = {
    'percentage': PercentageStrategy,
    'fixed': FixedStrategy,
    'tiered': TieredStrategy,
    'remainder': RemainderStrategy,
    'withdrawal_fee': WithdrawalFeeStrategy,
}


def CreateStrategy(participant_rule: dict, rate_table: Optional[RateTable] = None) -> BaseSplitStrategy:
    split_type = participant_rule.get('Type') or participant_rule.get('type', 'percentage')
    strategy_class = STRATEGY_MAP.get(split_type, PercentageStrategy)
    return strategy_class(participant_rule, rate_table)

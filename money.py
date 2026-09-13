"""模块：金额安全换算工具

提供统一的金额转换与四舍五入规范，禁止 float 参与元/分转换。
所有金额运算一律基于 Decimal，避免浮点精度丢失。

常用 API：
    from gvsdsdk.money import ToDecimal, QuantizeYuan, YuanToFen, FenToYuan, FormatYuan

    amount = ToDecimal('25.005')          # Decimal('25.005')
    yuan   = QuantizeYuan(amount)         # Decimal('25.01')  银行家舍入
    fen    = YuanToFen(amount)            # 2501  整数分
    yuan2  = FenToYuan(fen)               # Decimal('25.01')
    text   = FormatYuan(amount)           # '25.01'
"""
from decimal import Decimal, ROUND_HALF_EVEN, InvalidOperation
from functools import wraps

# 元精度（2 位小数），分精度（0 位小数）
_YUAN_QUANT = Decimal('0.01')
_FEN_QUANT = Decimal('1')


def ToDecimal(value, default=Decimal('0')):
    """安全转 Decimal，非法值返回 default。

    禁止 float 直接转换（会引入精度误差），float 先转 str 再转 Decimal。
    """
    if value is None or value == '':
        return default
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return default


def QuantizeYuan(value, rounding=ROUND_HALF_EVEN):
    """按元精度（2 位小数）四舍五入，默认银行家舍入。"""
    return ToDecimal(value).quantize(_YUAN_QUANT, rounding=rounding)


def YuanToFen(value):
    """元 → 分（整数）。先量化到 2 位小数再乘 100，避免精度漂移。"""
    return int((QuantizeYuan(value) * 100).to_integral_value(rounding=ROUND_HALF_EVEN))


def FenToYuan(fen):
    """分 → 元（Decimal，2 位小数）。"""
    return QuantizeYuan(ToDecimal(fen) / 100)


def FormatYuan(value, with_comma=False):
    """格式化元金额为字符串。

    with_comma=True 时返回千分位格式（如 '1,234.56'）。
    """
    q = QuantizeYuan(value)
    if with_comma:
        return f'{q:,.2f}'
    return str(q)


def SafeDecimalOperation(default=Decimal('0')):
    """装饰器：函数内任何 Decimal 异常均返回 default。

    适用于聚合统计等不能因单条脏数据中断的场景。
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except (InvalidOperation, TypeError, ValueError, ArithmeticError):
                return default
        return wrapper
    return decorator


def AddYuan(*values):
    """多个金额相加并量化到元。空入参返回 Decimal('0.00')。"""
    total = Decimal('0')
    for v in values:
        total += ToDecimal(v)
    return QuantizeYuan(total)


def MultiplyYuan(unit_price, quantity):
    """单价 × 数量 → 量化到元的金额。quantity 可为 Decimal/int/str。"""
    return QuantizeYuan(ToDecimal(unit_price) * ToDecimal(quantity))

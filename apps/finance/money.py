"""محاسبات پولی قطعی (deterministic): همه مبالغ ریال صحیح، درصدها Decimal، گرد کردن نیم به بالا.

هیچ محاسبه مالی از float استفاده نمی‌کند تا نتیجه روی همه سرورها و در هر بار محاسبه دوباره یکسان باشد.
"""

from decimal import ROUND_HALF_UP, Decimal

ONE = Decimal("1")


def to_rial(value):
    return int(Decimal(value).quantize(ONE, rounding=ROUND_HALF_UP))


def percent_of(amount, percent):
    """درصد مبلغ؛ منفی هم درست گرد می‌شود (قرینه)."""
    amount = int(amount)
    sign = -1 if amount < 0 else 1
    return sign * to_rial(Decimal(abs(amount)) * Decimal(percent) / Decimal(100))


def prorate(amount, numerator, denominator):
    if not denominator:
        return 0
    sign = -1 if amount < 0 else 1
    return sign * to_rial(Decimal(abs(int(amount))) * Decimal(numerator) / Decimal(denominator))


def split_proportional(total, weights):
    """تقسیم total به نسبت weights با روش بزرگ‌ترین باقی‌مانده؛ جمع خروجی دقیقاً total است.

    ترتیب ثابت ورودی تعیین‌کننده تساوی‌هاست، پس برای ورودی یکسان همیشه خروجی یکسان است.
    """
    total = int(total)
    weights = [max(int(w), 0) for w in weights]
    whole = sum(weights)
    if not weights:
        return []
    if whole == 0:
        return [0] * len(weights)
    sign = -1 if total < 0 else 1
    remaining = abs(total)
    shares, remainders = [], []
    for index, weight in enumerate(weights):
        exact = Decimal(remaining) * Decimal(weight) / Decimal(whole)
        floor = int(exact)
        shares.append(floor)
        remainders.append((exact - floor, -index))
    leftover = remaining - sum(shares)
    for _, neg_index in sorted(remainders, reverse=True)[:leftover]:
        shares[-neg_index] += 1
    return [sign * share for share in shares]

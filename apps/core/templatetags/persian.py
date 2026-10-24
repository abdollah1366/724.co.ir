import datetime

import jdatetime
from django import template
from django.utils import timezone

register = template.Library()

EN_TO_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


@register.filter
def fa_digits(value):
    return str(value).translate(EN_TO_FA)


@register.filter
def jalali(value, fmt="%A %d %B %Y"):
    """تاریخ میلادی را به تاریخ شمسی با ارقام فارسی تبدیل می‌کند."""
    if not value:
        return ""
    if isinstance(value, datetime.datetime):
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        converted = jdatetime.datetime.fromgregorian(datetime=value, locale=jdatetime.FA_LOCALE)
    elif isinstance(value, datetime.date):
        converted = jdatetime.date.fromgregorian(date=value, locale=jdatetime.FA_LOCALE)
    else:
        return value
    # روز ماه بدون صفر ابتدایی («۱ آبان» به‌جای «۰۱ آبان»)
    fmt = fmt.replace("%d", str(converted.day))
    return converted.strftime(fmt).translate(EN_TO_FA)


@register.filter
def toman(value):
    """نمایش مبلغ ریالی به تومان با جداکننده هزارگان."""
    if value in (None, ""):
        return ""
    return f"{int(value) // 10:,}".replace(",", "٬").translate(EN_TO_FA) + " تومان"


@register.filter
def rial(value):
    """نمایش مبلغ ریالی با جداکننده هزارگان و ارقام فارسی (منفی با علامت)."""
    if value in (None, ""):
        return ""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return value
    text = f"{abs(number):,}".replace(",", "٬").translate(EN_TO_FA)
    return f"−{text}" if number < 0 else text

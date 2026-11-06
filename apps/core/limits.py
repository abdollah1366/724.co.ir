"""سقف ارسال فرم‌های عمومی (بدون ورود) از هر IP؛ جلوی پر کردن همه نوبت‌ها، هرزنامه و عضویت جعلی را می‌گیرد."""

from django.conf import settings
from django.contrib import messages

from apps.audit.context import client_ip

LIMIT_MESSAGE = "تعداد درخواست‌های شما از این دستگاه در یک ساعت گذشته زیاد بوده است. کمی بعد دوباره تلاش کنید یا تماس بگیرید."


def public_form_blocked(request, name):
    """True اگر این IP سقف ارسال این فرم را در یک ساعت گذشته پر کرده باشد (و پیام خطا را می‌گذارد)."""
    from apps.lab.services.portal import rate_limited

    if rate_limited(f"form:{name}:{client_ip(request)}", limit=settings.PUBLIC_FORM_LIMIT_PER_HOUR):
        messages.error(request, LIMIT_MESSAGE)
        return True
    return False

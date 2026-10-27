from django.conf import settings
from django.core.checks import Error, Tags, register

from .payments import GATEWAYS


@register(Tags.security, deploy=True)
def payment_gateway_check(app_configs, **kwargs):
    errors = []
    if settings.PAYMENT_GATEWAY not in GATEWAYS:
        errors.append(Error(f"PAYMENT_GATEWAY نامعتبر است: {settings.PAYMENT_GATEWAY}", id="telemed.E001"))
    elif settings.PAYMENT_GATEWAY == "sandbox" and not settings.DEBUG:
        errors.append(Error(
            "درگاه آزمایشی (sandbox) در محیط واقعی مجاز نیست؛ بیمار بدون پرداخت مشاوره پولی می‌گیرد.",
            hint="PAYMENT_GATEWAY=none یا درگاه دارای مجوز شاپرک را تنظیم کنید.", id="telemed.E002",
        ))
    return errors

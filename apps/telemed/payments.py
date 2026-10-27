"""درگاه پرداخت با سرویس‌دهنده قابل تعویض (هنوز درگاه دارای مجوز شاپرک انتخاب نشده است).

PAYMENT_GATEWAY=none    (پیش‌فرض): پرداخت اینترنتی خاموش است؛ کارکنان پرداخت را در پنل «ثبت دستی» می‌کنند.
PAYMENT_GATEWAY=sandbox: درگاه آزمایشی برای توسعه و آزمون. در محیط واقعی (DEBUG خاموش) بررسی check --deploy خطا می‌دهد.

برای درگاه واقعی (زرین‌پال، آیدی‌پی، به‌پرداخت و ...) یک کلاس با دو متد بنویسید و در GATEWAYS اضافه کنید:
    start(payment, callback_url) -> آدرسی که بیمار برای پرداخت به آن هدایت می‌شود
    verify(payment, request)     -> (موفق؟, کد پیگیری بانکی)
verify باید مبلغ را از خود درگاه استعلام و با payment.amount مقایسه کند، نه از پارامترهای آدرس بازگشت.
"""

from django.conf import settings
from django.urls import reverse


class GatewayError(Exception):
    pass


class NoGateway:
    name = "none"
    online = False

    def start(self, payment, callback_url):
        raise GatewayError("پرداخت اینترنتی هنوز فعال نیست.")

    def verify(self, payment, request):
        return False, ""


class SandboxGateway:
    """فقط برای توسعه و آزمون؛ هیچ پولی جابه‌جا نمی‌شود."""

    name = "sandbox"
    online = True

    def start(self, payment, callback_url):
        return reverse("telemed:sandbox_pay", args=[payment.authority])

    def verify(self, payment, request):
        if request.GET.get("result") == "ok":
            return True, f"SANDBOX-{payment.pk}"
        return False, ""


GATEWAYS = {"none": NoGateway, "sandbox": SandboxGateway}


def gateway():
    return GATEWAYS[settings.PAYMENT_GATEWAY]()

"""ارسال پیامک با سرویس‌دهنده قابل تعویض.

SMS_BACKEND=console  (پیش‌فرض): پیامک فقط در لاگ و جدول پیامک‌ها ثبت می‌شود؛ برای توسعه و آزمون.
SMS_BACKEND=kavenegar: با SMS_API_KEY و SMS_SENDER از API کاوه‌نگار ارسال می‌شود.
برای سرویس‌دهنده دیگر یک کلاس با متد send(mobile, text) بنویسید و در BACKENDS اضافه کنید.
"""

import json
import logging
import urllib.parse
import urllib.request

from django.conf import settings

from .models import SmsMessage

logger = logging.getLogger(__name__)

# برای آزمون‌ها: آخرین پیامک‌های ارسالی با سرویس console
outbox = []


class SmsError(Exception):
    pass


class ConsoleBackend:
    def send(self, mobile, text):
        outbox.append({"mobile": mobile, "text": text})
        logger.info("SMS to %s: %s", mobile, text)
        return f"console-{len(outbox)}"


class KavenegarBackend:
    url = "https://api.kavenegar.com/v1/{key}/sms/send.json"

    def send(self, mobile, text):
        key = settings.SMS_API_KEY
        if not key:
            raise SmsError("SMS_API_KEY تنظیم نشده است.")
        data = urllib.parse.urlencode({"receptor": mobile, "message": text, "sender": settings.SMS_SENDER}).encode()
        try:
            with urllib.request.urlopen(self.url.format(key=key), data=data, timeout=10) as response:
                body = json.loads(response.read().decode())
        except Exception as exc:  # خطای شبکه یا پاسخ نامعتبر
            raise SmsError(str(exc)) from exc
        if body.get("return", {}).get("status") != 200:
            raise SmsError(body.get("return", {}).get("message", "خطای نامشخص"))
        return str(body["entries"][0]["messageid"])


BACKENDS = {"console": ConsoleBackend, "kavenegar": KavenegarBackend}


def send_sms(mobile, text, kind):
    """ارسال و ثبت پیامک. خطای سرویس‌دهنده ثبت می‌شود ولی جریان کار را متوقف نمی‌کند."""
    backend = BACKENDS[settings.SMS_BACKEND]()
    try:
        provider_id = backend.send(mobile, text)
    except SmsError as exc:
        logger.warning("SMS failed (%s): %s", kind, exc)
        return SmsMessage.objects.create(mobile=mobile, kind=kind, text=text[:500], status=SmsMessage.Status.FAILED, error=str(exc)[:300])
    return SmsMessage.objects.create(mobile=mobile, kind=kind, text=text[:500], status=SmsMessage.Status.SENT, provider_id=provider_id)

"""کد یک‌بارمصرف دسترسی بیمار به جواب آزمایش یا اتصال پرونده به حساب کاربری."""

import hashlib
import hmac

from django.conf import settings
from django.db import models

from .orders import LabOrder
from .patients import LabPatient


def hash_code(code):
    return hmac.new(settings.SECRET_KEY.encode(), code.encode(), hashlib.sha256).hexdigest()


class AccessCode(models.Model):
    class Purpose(models.TextChoices):
        RESULT = "result", "مشاهده جواب"
        LINK = "link", "اتصال پرونده به حساب"

    purpose = models.CharField("کاربرد", max_length=6, choices=Purpose.choices)
    patient = models.ForeignKey(LabPatient, on_delete=models.CASCADE, related_name="access_codes", verbose_name="بیمار")
    order = models.ForeignKey(LabOrder, on_delete=models.CASCADE, null=True, blank=True, related_name="+", verbose_name="پذیرش")
    code_hash = models.CharField("هش کد", max_length=64)
    attempts = models.PositiveSmallIntegerField("تلاش ناموفق", default=0)
    created_at = models.DateTimeField("زمان ارسال", auto_now_add=True)
    expires_at = models.DateTimeField("انقضا")
    used_at = models.DateTimeField("زمان استفاده", null=True, blank=True)
    ip = models.CharField("IP", max_length=45, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "کد یک‌بارمصرف"
        verbose_name_plural = "کدهای یک‌بارمصرف"

    def matches(self, code):
        return hmac.compare_digest(self.code_hash, hash_code(code))

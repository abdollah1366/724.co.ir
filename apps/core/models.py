from django.db import models

from .validators import validate_mobile


class ContactMessage(models.Model):
    name = models.CharField("نام", max_length=120)
    mobile = models.CharField("موبایل", max_length=11, validators=[validate_mobile])
    subject = models.CharField("موضوع", max_length=200)
    message = models.TextField("پیام", max_length=3000)
    is_read = models.BooleanField("خوانده شده", default=False)
    created_at = models.DateTimeField("زمان ارسال", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "پیام تماس"
        verbose_name_plural = "پیام‌های تماس"

    def __str__(self):
        return f"{self.name} - {self.subject}"


class SmsMessage(models.Model):
    """گزارش پیامک‌های ارسالی. متن پیامک‌ها هرگز نام آزمایش یا جواب را ندارد."""

    class Status(models.TextChoices):
        SENT = "sent", "ارسال شد"
        FAILED = "failed", "ناموفق"

    mobile = models.CharField("موبایل", max_length=11)
    kind = models.CharField("نوع", max_length=30)
    text = models.CharField("متن", max_length=500)
    status = models.CharField("وضعیت", max_length=6, choices=Status.choices)
    provider_id = models.CharField("شناسه ارسال", max_length=60, blank=True)
    error = models.CharField("خطا", max_length=300, blank=True)
    created_at = models.DateTimeField("زمان", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "پیامک"
        verbose_name_plural = "پیامک‌های ارسالی"

    def __str__(self):
        return f"{self.kind} → {self.mobile}"

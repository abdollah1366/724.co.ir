import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

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


class FeatureFlag(models.Model):
    """مقدار ذخیره‌شده یک قابلیت قابل روشن/خاموش. تعریف و پیش‌فرض‌ها در apps/core/flags.py است.

    نبود ردیف یعنی «مقدار پیش‌فرض». خاموش کردن هیچ داده‌ای را پاک نمی‌کند.
    """

    key = models.CharField("کلید", max_length=80, unique=True)
    enabled = models.BooleanField("فعال")
    note = models.CharField("علت آخرین تغییر", max_length=300, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name="تغییردهنده"
    )
    updated_at = models.DateTimeField("آخرین تغییر", auto_now=True)

    class Meta:
        ordering = ["key"]
        verbose_name = "قابلیت (feature flag)"
        verbose_name_plural = "قابلیت‌ها و اتصال‌های ماژول‌ها"

    def __str__(self):
        return f"{self.key} = {'روشن' if self.enabled else 'خاموش'}"


class OutboxEvent(models.Model):
    """رویداد دامنه در صندوق خروجی (transactional outbox).

    ماژول تولیدکننده رویداد را در همان تراکنش داده خودش ذخیره می‌کند؛ پس از commit یا با دستور زمان‌بندی‌شده
    به handler مصرف‌کننده (مثلاً مالی) داده می‌شود. ترکیب نوع + منبع + کلید یکتاست تا رویداد تکراری ساخته نشود.
    payload فقط حداقل داده لازم را دارد و هرگز متن بالینی ندارد.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "در انتظار پردازش"
        PROCESSED = "processed", "پردازش شد"
        FAILED = "failed", "ناموفق (تلاش مجدد)"
        DEAD = "dead", "متوقف (نیاز به بررسی)"
        IGNORED = "ignored", "بدون مصرف‌کننده"

    event_id = models.UUIDField("شناسه رویداد", default=uuid.uuid4, unique=True, editable=False)
    event_type = models.CharField("نوع رویداد", max_length=80, db_index=True)
    schema_version = models.PositiveSmallIntegerField("نسخه ساختار", default=1)
    occurred_at = models.DateTimeField("زمان وقوع", default=timezone.now)
    source_type = models.CharField("نوع منبع", max_length=60)
    source_id = models.CharField("شناسه منبع", max_length=64)
    dedupe_key = models.CharField("کلید یکتایی", max_length=64, blank=True, default="")
    payload = models.JSONField("داده", default=dict, blank=True)
    correlation_id = models.CharField("شناسه همبستگی", max_length=64, blank=True)
    status = models.CharField("وضعیت", max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    attempts = models.PositiveSmallIntegerField("تعداد تلاش", default=0)
    next_attempt_at = models.DateTimeField("تلاش بعدی", default=timezone.now, db_index=True)
    last_error = models.CharField("آخرین خطا", max_length=500, blank=True)
    processed_at = models.DateTimeField("زمان پردازش", null=True, blank=True)
    result_ref = models.CharField("نتیجه (شماره سند / صورتحساب)", max_length=120, blank=True)
    created_at = models.DateTimeField("زمان ثبت", auto_now_add=True)

    class Meta:
        ordering = ["-id"]
        verbose_name = "رویداد یکپارچگی"
        verbose_name_plural = "رویدادهای یکپارچگی (صف خروجی)"
        permissions = [("retry_outboxevent", "تلاش مجدد رویدادهای ناموفق")]
        constraints = [
            models.UniqueConstraint(fields=["event_type", "source_type", "source_id", "dedupe_key"], name="unique_outbox_event")
        ]
        indexes = [models.Index(fields=["status", "next_attempt_at"], name="outbox_due_idx")]

    def __str__(self):
        return f"{self.event_type} {self.source_type}#{self.source_id}"

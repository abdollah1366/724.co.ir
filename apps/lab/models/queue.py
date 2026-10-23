"""صف و فراخوان: نوبت حضوری روزانه، باجه‌ها و نمایش روی مانیتور سالن انتظار.

روی نمایشگر عمومی فقط شماره نوبت و نام باجه می‌آید؛ نام و مشخصات بیمار هرگز در صف ذخیره یا نمایش داده نمی‌شود.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

from .patients import SamplingAppointment


class QueueService(models.Model):
    name = models.CharField("نام خدمت", max_length=60, help_text="روی دکمه کیوسک نمایش داده می‌شود؛ مثل «پذیرش و صندوق»")
    code = models.CharField("حرف نوبت", max_length=2, unique=True, help_text="پیشوند شماره نوبت؛ مثل A")
    description = models.CharField("توضیح کوتاه", max_length=120, blank=True)
    order = models.PositiveSmallIntegerField("ترتیب", default=0)
    is_active = models.BooleanField("فعال", default=True)

    class Meta:
        ordering = ["order", "code"]
        verbose_name = "خدمت صف"
        verbose_name_plural = "خدمت‌های صف"

    def __str__(self):
        return f"{self.code} · {self.name}"


class QueueCounter(models.Model):
    name = models.CharField("نام باجه", max_length=40, help_text="همان چیزی که روی مانیتور خوانده می‌شود؛ مثل «باجه ۲»")
    services = models.ManyToManyField(QueueService, related_name="counters", verbose_name="خدمت‌های این باجه")
    order = models.PositiveSmallIntegerField("ترتیب", default=0)
    is_active = models.BooleanField("فعال", default=True)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "باجه"
        verbose_name_plural = "باجه‌ها"

    def __str__(self):
        return self.name


class QueueTicket(models.Model):
    class Status(models.TextChoices):
        WAITING = "waiting", "در انتظار"
        CALLED = "called", "فراخوانده‌شده"
        DONE = "done", "انجام شد"
        SKIPPED = "skipped", "حاضر نشد"

    class Priority(models.IntegerChoices):
        NORMAL = 0, "عادی"
        APPOINTMENT = 1, "نوبت اینترنتی"
        SPECIAL = 2, "اولویت‌دار (سالمند، باردار، معلول، کودک، اورژانسی)"

    date = models.DateField("تاریخ", default=timezone.localdate, db_index=True)
    service = models.ForeignKey(QueueService, on_delete=models.PROTECT, related_name="tickets", verbose_name="خدمت")
    number = models.PositiveIntegerField("شماره")
    priority = models.PositiveSmallIntegerField("اولویت", choices=Priority.choices, default=Priority.NORMAL)
    status = models.CharField("وضعیت", max_length=8, choices=Status.choices, default=Status.WAITING, db_index=True)
    appointment = models.ForeignKey(
        SamplingAppointment, on_delete=models.SET_NULL, null=True, blank=True, related_name="tickets", verbose_name="نوبت اینترنتی"
    )
    counter = models.ForeignKey(QueueCounter, on_delete=models.SET_NULL, null=True, blank=True, related_name="tickets", verbose_name="باجه")
    issued_at = models.DateTimeField("زمان صدور", auto_now_add=True)
    queued_at = models.DateTimeField("ورود به صف فعلی", default=timezone.now, help_text="با انتقال به خدمت دیگر به‌روز می‌شود")
    called_at = models.DateTimeField("زمان فراخوان", null=True, blank=True)
    finished_at = models.DateTimeField("زمان پایان", null=True, blank=True)
    recall_count = models.PositiveSmallIntegerField("تکرار فراخوان", default=0)
    transferred_from = models.ForeignKey(
        QueueService, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name="منتقل‌شده از"
    )
    called_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name="فراخوان توسط"
    )

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [models.UniqueConstraint(fields=["date", "service", "number"], name="lab_queue_ticket_daily_number")]
        indexes = [models.Index(fields=["date", "service", "status"])]
        verbose_name = "نوبت حضوری"
        verbose_name_plural = "نوبت‌های حضوری"

    def __str__(self):
        return self.label

    @property
    def label(self):
        return f"{self.service.code}{self.number:03d}"

    @property
    def wait_minutes(self):
        end = self.called_at or timezone.now()
        return max(int((end - self.queued_at).total_seconds() // 60), 0)

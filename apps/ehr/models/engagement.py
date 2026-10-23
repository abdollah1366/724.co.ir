"""یادآور ویزیت و دارو، و صف حضوری مطب پزشکان با نمایشگر سالن انتظار.

پیامک یادآوری فقط با رضایت بیمار فرستاده می‌شود و نام پزشک، دارو یا بیماری در آن نیست.
در صف فقط شماره نوبت، نام پزشک و اتاق روی مانیتور می‌آید؛ نام بیمار هرگز نمایش داده نمی‌شود.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.clinic.models import Doctor

from .patients import Patient


class Reminder(models.Model):
    class Kind(models.TextChoices):
        VISIT = "visit", "ویزیت پیگیری"
        REFILL = "refill", "تمدید دارو"
        LAB = "lab", "انجام آزمایش"
        THERAPY = "therapy", "جلسه درمان"
        OTHER = "other", "سایر"

    class Status(models.TextChoices):
        PENDING = "pending", "در انتظار ارسال"
        SENT = "sent", "پیامک ارسال شد"
        NO_CONSENT = "no_consent", "ارسال نشد (بدون رضایت یا موبایل)"
        FAILED = "failed", "ارسال ناموفق"
        CANCELLED = "cancelled", "لغو شد"

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="reminders", verbose_name="بیمار")
    kind = models.CharField("نوع", max_length=10, choices=Kind.choices)
    due_date = models.DateField("موعد مراجعه")
    send_on = models.DateField("تاریخ ارسال پیامک", db_index=True)
    note = models.CharField("یادداشت داخلی", max_length=200, blank=True, help_text="در پیامک نمی‌آید؛ فقط در پرونده دیده می‌شود.")
    doctor = models.ForeignKey(Doctor, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name="پزشک")
    encounter = models.ForeignKey("ehr.Encounter", on_delete=models.SET_NULL, null=True, blank=True, related_name="reminders",
                                  verbose_name="ویزیت")
    prescription = models.ForeignKey("ehr.Prescription", on_delete=models.SET_NULL, null=True, blank=True, related_name="reminders",
                                     verbose_name="نسخه")
    status = models.CharField("وضعیت", max_length=10, choices=Status.choices, default=Status.PENDING)
    sent_at = models.DateTimeField("زمان ارسال", null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name="ثبت‌کننده")
    created_at = models.DateTimeField("زمان ثبت", auto_now_add=True)

    class Meta:
        ordering = ["due_date", "id"]
        verbose_name = "یادآور"
        verbose_name_plural = "یادآورها"

    def __str__(self):
        return f"{self.get_kind_display()} {self.patient.full_name} {self.due_date}"


class ClinicTicket(models.Model):
    """نوبت حضوری روز برای یک پزشک. شماره هر پزشک هر روز از ۱ شروع می‌شود."""

    class Status(models.TextChoices):
        WAITING = "waiting", "در انتظار"
        CALLED = "called", "فراخوانده‌شده"
        DONE = "done", "ویزیت شد"
        SKIPPED = "skipped", "حاضر نشد"
        CANCELLED = "cancelled", "لغو"

    class Priority(models.IntegerChoices):
        NORMAL = 0, "عادی"
        SPECIAL = 2, "اولویت‌دار (سالمند، باردار، معلول، کودک، اورژانسی)"

    date = models.DateField("تاریخ", default=timezone.localdate, db_index=True)
    doctor = models.ForeignKey(Doctor, on_delete=models.PROTECT, related_name="tickets", verbose_name="پزشک")
    number = models.PositiveIntegerField("شماره")
    priority = models.PositiveSmallIntegerField("اولویت", choices=Priority.choices, default=Priority.NORMAL)
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, null=True, blank=True, related_name="tickets", verbose_name="بیمار")
    appointment = models.OneToOneField("appointments.Appointment", on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name="ticket", verbose_name="نوبت")
    status = models.CharField("وضعیت", max_length=10, choices=Status.choices, default=Status.WAITING, db_index=True)
    checked_in_at = models.DateTimeField("زمان پذیرش", default=timezone.now)
    called_at = models.DateTimeField("زمان فراخوان", null=True, blank=True)
    finished_at = models.DateTimeField("زمان پایان", null=True, blank=True)
    recall_count = models.PositiveSmallIntegerField("تکرار فراخوان", default=0)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+", verbose_name="پذیرش")

    class Meta:
        ordering = ["-date", "-priority", "checked_in_at"]
        constraints = [models.UniqueConstraint(fields=["date", "doctor", "number"], name="clinic_ticket_daily_number")]
        indexes = [models.Index(fields=["date", "doctor", "status"])]
        verbose_name = "نوبت حضوری مطب"
        verbose_name_plural = "صف مطب پزشکان"

    def __str__(self):
        return f"{self.label} {self.doctor}"

    @property
    def label(self):
        return f"{self.doctor.queue_code}{self.number:02d}" if self.doctor.queue_code else str(self.number)

    @property
    def wait_minutes(self):
        end = self.called_at or timezone.now()
        return max(int((end - self.checked_in_at).total_seconds() // 60), 0)

import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.clinic.models import Doctor
from apps.core.validators import validate_mobile, validate_national_code


def generate_tracking_code():
    # ۸ رقم تصادفی امن؛ همراه با موبایل برای پیگیری استفاده می‌شود.
    return "".join(secrets.choice("0123456789") for _ in range(8))


class AppointmentQuerySet(models.QuerySet):
    def active(self):
        return self.exclude(status=Appointment.Status.CANCELLED)


class Appointment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "در انتظار تأیید"
        CONFIRMED = "confirmed", "تأیید شده"
        CANCELLED = "cancelled", "لغو شده"
        DONE = "done", "انجام شده"

    doctor = models.ForeignKey(Doctor, on_delete=models.PROTECT, related_name="appointments", verbose_name="پزشک")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="appointments",
        verbose_name="حساب کاربری",
    )
    date = models.DateField("تاریخ")
    time = models.TimeField("ساعت")
    patient_full_name = models.CharField("نام و نام خانوادگی بیمار", max_length=150)
    national_code = models.CharField("کد ملی", max_length=10, validators=[validate_national_code])
    mobile = models.CharField("موبایل", max_length=11, validators=[validate_mobile])
    status = models.CharField("وضعیت", max_length=12, choices=Status.choices, default=Status.PENDING)
    patient = models.ForeignKey(
        "ehr.Patient", on_delete=models.PROTECT, null=True, blank=True, related_name="appointments",
        verbose_name="پرونده بیمار", help_text="با کد ملی خودکار وصل می‌شود.",
    )
    tracking_code = models.CharField("کد پیگیری", max_length=8, unique=True, default=generate_tracking_code, editable=False)
    consent_at = models.DateTimeField("زمان رضایت به پردازش اطلاعات")
    reminder_sent_at = models.DateTimeField("ارسال پیامک یادآوری", null=True, blank=True, editable=False)
    created_at = models.DateTimeField("زمان ثبت", auto_now_add=True)
    updated_at = models.DateTimeField("آخرین تغییر", auto_now=True)

    objects = AppointmentQuerySet.as_manager()

    class Meta:
        ordering = ["-date", "-time"]
        verbose_name = "نوبت"
        verbose_name_plural = "نوبت‌ها"
        constraints = [
            # یک زمان از یک پزشک فقط یک نوبت فعال می‌تواند داشته باشد.
            models.UniqueConstraint(
                fields=["doctor", "date", "time"],
                condition=~models.Q(status="cancelled"),
                name="unique_active_appointment_slot",
            )
        ]
        indexes = [models.Index(fields=["doctor", "date"])]

    def __str__(self):
        return f"{self.tracking_code} - {self.doctor}"

    @property
    def masked_national_code(self):
        return f"{self.national_code[:3]}****{self.national_code[-3:]}"

    @property
    def can_cancel(self):
        if self.status not in (self.Status.PENDING, self.Status.CONFIRMED):
            return False
        starts_at = timezone.make_aware(timezone.datetime.combine(self.date, self.time))
        return starts_at > timezone.now()

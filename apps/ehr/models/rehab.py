"""خدمات توانبخشی و روان: فیزیوتراپی، گفتاردرمانی، کاردرمانی، روانشناسی، شنوایی‌شناسی و تغذیه.

هر بیمار برای هر رشته یک «برنامه درمان» با اهداف و تعداد جلسات دارد؛ هر جلسه یادداشت و کد خدمت دارد و پس از امضا قفل می‌شود.
یادداشت‌های روانشناسی محرمانه‌تر است: فقط درمانگر همان برنامه و مسئول فنی متن را می‌بینند، مگر درمانگر اجازه اشتراک بدهد.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.clinic.models import Doctor

from .catalog import RVUCoefficient, ServiceCode
from .patients import Patient


class Discipline(models.TextChoices):
    PHYSIO = "physio", "فیزیوتراپی"
    SPEECH = "speech", "گفتاردرمانی"
    OT = "ot", "کاردرمانی"
    PSYCHOLOGY = "psychology", "روانشناسی و مشاوره"
    AUDIOLOGY = "audiology", "شنوایی‌شناسی"
    NUTRITION = "nutrition", "تغذیه و رژیم درمانی"


CONFIDENTIAL_DISCIPLINES = {Discipline.PSYCHOLOGY}


class TreatmentPlan(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "در حال درمان"
        COMPLETED = "completed", "پایان درمان"
        DISCONTINUED = "discontinued", "قطع درمان"

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="treatment_plans", verbose_name="بیمار")
    discipline = models.CharField("رشته", max_length=12, choices=Discipline.choices)
    therapist = models.ForeignKey(Doctor, on_delete=models.PROTECT, related_name="treatment_plans", verbose_name="درمانگر")
    referral = models.ForeignKey("ehr.Referral", on_delete=models.SET_NULL, null=True, blank=True, related_name="plans",
                                 verbose_name="ارجاع")
    diagnosis = models.CharField("تشخیص / مشکل", max_length=300)
    icd = models.ForeignKey("ehr.ICD10Code", on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name="کد ICD-10")
    goals = models.TextField("اهداف درمان")
    planned_sessions = models.PositiveSmallIntegerField("تعداد جلسات پیش‌بینی‌شده", default=10)
    frequency = models.CharField("تواتر جلسات", max_length=80, blank=True, help_text="مثلاً ۳ جلسه در هفته")
    start_date = models.DateField("شروع", default=timezone.localdate)
    status = models.CharField("وضعیت", max_length=12, choices=Status.choices, default=Status.ACTIVE)
    discharge_summary = models.TextField("خلاصه پایان درمان", blank=True)
    share_with_physicians = models.BooleanField(
        "متن یادداشت‌ها برای پزشکان معالج قابل مشاهده باشد", default=True,
        help_text="برای روانشناسی پیش‌فرض خاموش است؛ با رضایت بیمار روشن کنید.",
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name="ایجادکننده")
    created_at = models.DateTimeField("زمان ایجاد", auto_now_add=True)

    class Meta:
        ordering = ["-start_date", "-id"]
        verbose_name = "برنامه درمان توانبخشی"
        verbose_name_plural = "برنامه‌های درمان توانبخشی"

    def __str__(self):
        return f"{self.get_discipline_display()} · {self.patient.full_name}"

    @property
    def is_confidential(self):
        return self.discipline in CONFIDENTIAL_DISCIPLINES and not self.share_with_physicians

    @property
    def done_sessions(self):
        return self.sessions.filter(attendance=TherapySession.Attendance.ATTENDED).count()

    def can_read_notes(self, user):
        if not self.is_confidential or user.has_perm("ehr.view_all_records"):
            return True
        doctor = getattr(user, "doctor", None)
        return doctor is not None and doctor.pk == self.therapist_id


class TherapySession(models.Model):
    class Attendance(models.TextChoices):
        ATTENDED = "attended", "انجام شد"
        NO_SHOW = "no_show", "غیبت بیمار"
        CANCELLED = "cancelled", "لغو"

    plan = models.ForeignKey(TreatmentPlan, on_delete=models.PROTECT, related_name="sessions", verbose_name="برنامه درمان")
    number = models.PositiveSmallIntegerField("جلسه شماره", editable=False)
    held_at = models.DateTimeField("زمان جلسه", default=timezone.now)
    attendance = models.CharField("حضور", max_length=10, choices=Attendance.choices, default=Attendance.ATTENDED)
    subjective = models.TextField("گزارش بیمار / خانواده", blank=True)
    intervention = models.TextField("اقدامات درمانی جلسه", blank=True)
    response = models.TextField("پاسخ بیمار و پیشرفت", blank=True)
    home_program = models.TextField("تمرین در منزل / توصیه", blank=True)
    pain_score = models.PositiveSmallIntegerField("شدت درد (۰ تا ۱۰)", null=True, blank=True)
    service_code = models.ForeignKey(ServiceCode, on_delete=models.PROTECT, null=True, blank=True, related_name="+",
                                     verbose_name="کد ملی خدمت")
    sector = models.CharField("بخش تعرفه", max_length=10, choices=RVUCoefficient.Sector.choices, default=RVUCoefficient.Sector.PRIVATE)
    unit_tariff = models.PositiveBigIntegerField("تعرفه (ریال)", null=True, blank=True, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name="ثبت‌کننده")
    signed_at = models.DateTimeField("زمان امضا", null=True, blank=True, editable=False)

    class Meta:
        ordering = ["plan", "number"]
        verbose_name = "جلسه درمان"
        verbose_name_plural = "جلسات درمان"
        constraints = [
            models.UniqueConstraint(fields=["plan", "number"], name="unique_session_number_per_plan"),
            models.CheckConstraint(condition=models.Q(pain_score__isnull=True) | models.Q(pain_score__lte=10), name="pain_score_0_10"),
        ]

    def __str__(self):
        return f"جلسه {self.number} {self.plan}"

    def save(self, *args, **kwargs):
        if self.pk and TherapySession.objects.filter(pk=self.pk, signed_at__isnull=False).exists():
            raise ValidationError("جلسه امضاشده قابل ویرایش نیست.")
        if not self.number:
            last = TherapySession.objects.filter(plan=self.plan).order_by("-number").values_list("number", flat=True).first()
            self.number = (last or 0) + 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("جلسه درمان حذف نمی‌شود.")

    def sign(self):
        TherapySession.objects.filter(pk=self.pk, signed_at__isnull=True).update(signed_at=timezone.now())
        self.refresh_from_db()


# حدود شدت پرسشنامه‌های استاندارد
SEVERITY_BANDS = {
    "phq9": [(4, "حداقل"), (9, "خفیف"), (14, "متوسط"), (19, "نسبتاً شدید"), (27, "شدید")],
    "gad7": [(4, "حداقل"), (9, "خفیف"), (14, "متوسط"), (21, "شدید")],
}
AUDIOGRAM_FREQUENCIES = (250, 500, 1000, 2000, 4000, 8000)
PTA_FREQUENCIES = (500, 1000, 2000, 4000)


class Assessment(models.Model):
    class Kind(models.TextChoices):
        AUDIOGRAM = "audiogram", "ادیوگرام (آستانه شنوایی)"
        PHQ9 = "phq9", "پرسشنامه افسردگی PHQ-9"
        GAD7 = "gad7", "پرسشنامه اضطراب GAD-7"
        VAS = "vas", "مقیاس درد VAS"
        ROM = "rom", "دامنه حرکتی (ROM)"
        SPEECH = "speech", "ارزیابی گفتار و زبان"
        NUTRITION = "nutrition", "ارزیابی تغذیه"
        OTHER = "other", "سایر"

    plan = models.ForeignKey(TreatmentPlan, on_delete=models.PROTECT, related_name="assessments", verbose_name="برنامه درمان")
    kind = models.CharField("نوع ارزیابی", max_length=10, choices=Kind.choices)
    assessed_at = models.DateTimeField("زمان", default=timezone.now)
    score = models.DecimalField("امتیاز / مقدار", max_digits=7, decimal_places=2, null=True, blank=True)
    data = models.JSONField("داده‌ها", default=dict, blank=True)
    notes = models.TextField("شرح", blank=True)
    document = models.ForeignKey("ehr.ExternalDocument", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
                                 verbose_name="فایل پیوست")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name="ثبت‌کننده")

    class Meta:
        ordering = ["-assessed_at"]
        verbose_name = "ارزیابی"
        verbose_name_plural = "ارزیابی‌ها"

    def __str__(self):
        return f"{self.get_kind_display()} {self.plan.patient.full_name}"

    @property
    def severity(self):
        bands = SEVERITY_BANDS.get(self.kind)
        if not bands or self.score is None:
            return ""
        return next((label for limit, label in bands if self.score <= limit), bands[-1][1])

    def pta(self, ear):
        """میانگین آستانه تون خالص در ۵۰۰ تا ۴۰۰۰ هرتز (dB HL)."""
        values = [self.data.get(ear, {}).get(str(f)) for f in PTA_FREQUENCIES]
        values = [v for v in values if v is not None]
        return round(sum(values) / len(values), 1) if len(values) == len(PTA_FREQUENCIES) else None

    @property
    def audiogram_rows(self):
        return [(label, [self.data.get(ear, {}).get(str(f)) for f in AUDIOGRAM_FREQUENCIES])
                for ear, label in (("right", "گوش راست"), ("left", "گوش چپ"))]

    @property
    def summary(self):
        if self.kind == self.Kind.AUDIOGRAM:
            right, left = self.pta("right"), self.pta("left")
            return f"PTA راست {right if right is not None else '—'} dB، چپ {left if left is not None else '—'} dB"
        if self.score is not None:
            text = f"امتیاز {self.score.normalize()}"
            return f"{text} ({self.severity})" if self.severity else text
        return ""

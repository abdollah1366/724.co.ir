"""ویزیت و سوابق بالینی: شرح حال SOAP، علائم حیاتی، تشخیص ICD-10، فهرست مشکلات، حساسیت، سوابق و ارجاع.

ویزیت پس از امضای پزشک قفل می‌شود و فقط «یادداشت تکمیلی» (addendum) با نام و زمان به آن اضافه می‌شود.
"""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.audit.documents import ImmutableRecord
from apps.clinic.models import Doctor

from .patients import Patient


class ICD10Code(models.Model):
    code = models.CharField("کد", max_length=10, unique=True)
    title = models.CharField("عنوان (انگلیسی)", max_length=300)
    title_fa = models.CharField("عنوان فارسی", max_length=300, blank=True)
    is_active = models.BooleanField("فعال", default=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "کد ICD-10"
        verbose_name_plural = "کدهای ICD-10"

    def __str__(self):
        return f"{self.code} {self.title_fa or self.title}"


class ChronicProgram(models.TextChoices):
    NONE = "", "—"
    DIABETES = "diabetes", "دیابت"
    HYPERTENSION = "hypertension", "فشار خون بالا"
    DYSLIPIDEMIA = "dyslipidemia", "چربی خون بالا"
    ASTHMA = "asthma", "آسم / COPD"
    THYROID = "thyroid", "بیماری تیروئید"
    OTHER = "other", "سایر بیماری‌های مزمن"


class Encounter(models.Model):
    class Kind(models.TextChoices):
        VISIT = "visit", "ویزیت"
        FOLLOW_UP = "follow_up", "پیگیری"
        CONSULT = "consult", "مشاوره"
        PROCEDURE = "procedure", "خدمت سرپایی / پروسیجر"
        TELE = "tele", "مشاوره تلفنی / آنلاین"

    class Status(models.TextChoices):
        OPEN = "open", "باز (در حال ویزیت)"
        SIGNED = "signed", "امضاشده"

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="encounters", verbose_name="بیمار")
    provider = models.ForeignKey(Doctor, on_delete=models.PROTECT, related_name="encounters", verbose_name="پزشک")
    appointment = models.OneToOneField(
        "appointments.Appointment", on_delete=models.PROTECT, null=True, blank=True, related_name="encounter",
        verbose_name="نوبت",
    )
    kind = models.CharField("نوع", max_length=10, choices=Kind.choices, default=Kind.VISIT)
    status = models.CharField("وضعیت", max_length=6, choices=Status.choices, default=Status.OPEN, editable=False)
    started_at = models.DateTimeField("زمان شروع", default=timezone.now)
    chief_complaint = models.CharField("شکایت اصلی", max_length=300, blank=True)
    subjective = models.TextField("شرح حال (S)", blank=True)
    objective = models.TextField("معاینه (O)", blank=True)
    assessment = models.TextField("ارزیابی (A)", blank=True)
    plan = models.TextField("برنامه درمان و توصیه‌ها (P)", blank=True)
    next_visit = models.DateField("تاریخ ویزیت بعدی", null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name="ایجادکننده")
    signed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name="امضاکننده",
        editable=False,
    )
    signed_at = models.DateTimeField("زمان امضا", null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-started_at", "-id"]
        verbose_name = "ویزیت"
        verbose_name_plural = "ویزیت‌ها"
        indexes = [models.Index(fields=["patient", "started_at"])]
        permissions = [("sign_encounter", "امضای ویزیت")]

    def __str__(self):
        return f"{self.get_kind_display()} {self.patient.full_name} - {self.provider}"

    @property
    def is_open(self):
        return self.status == self.Status.OPEN

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            old = Encounter.objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if old == self.Status.SIGNED and not getattr(self, "_signing", False):
                raise ValidationError("ویزیت امضاشده قابل ویرایش نیست؛ یادداشت تکمیلی اضافه کنید.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("ویزیت حذف نمی‌شود.")


class EncounterChild(models.Model):
    """ردیفی که به ویزیت وابسته است و پس از امضای ویزیت تغییر نمی‌کند."""

    class Meta:
        abstract = True

    def _locked(self):
        encounter_id = getattr(self, "encounter_id", None)
        return bool(encounter_id) and Encounter.objects.filter(pk=encounter_id, status=Encounter.Status.SIGNED).exists()

    def save(self, *args, **kwargs):
        if self._locked():
            raise ValidationError("ویزیت امضا شده و قابل تغییر نیست.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self._locked():
            raise ValidationError("ویزیت امضا شده و قابل تغییر نیست.")
        return super().delete(*args, **kwargs)


class EncounterAddendum(ImmutableRecord):
    encounter = models.ForeignKey(Encounter, on_delete=models.PROTECT, related_name="addenda", verbose_name="ویزیت")
    text = models.TextField("یادداشت تکمیلی / اصلاحیه")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name="کاربر")
    created_at = models.DateTimeField("زمان", auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "یادداشت تکمیلی ویزیت"
        verbose_name_plural = "یادداشت‌های تکمیلی ویزیت"


class VitalSigns(EncounterChild):
    class GlucoseKind(models.TextChoices):
        FASTING = "fasting", "ناشتا"
        RANDOM = "random", "غیرناشتا"
        POSTPRANDIAL = "pp2", "دو ساعت پس از غذا"

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="vitals", verbose_name="بیمار")
    encounter = models.ForeignKey(Encounter, on_delete=models.PROTECT, null=True, blank=True, related_name="vitals", verbose_name="ویزیت")
    recorded_at = models.DateTimeField("زمان اندازه‌گیری", default=timezone.now)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name="ثبت‌کننده")
    systolic = models.PositiveSmallIntegerField("فشار سیستول (mmHg)", null=True, blank=True)
    diastolic = models.PositiveSmallIntegerField("فشار دیاستول (mmHg)", null=True, blank=True)
    pulse = models.PositiveSmallIntegerField("نبض (در دقیقه)", null=True, blank=True)
    respiratory_rate = models.PositiveSmallIntegerField("تنفس (در دقیقه)", null=True, blank=True)
    temperature = models.DecimalField("دما (°C)", max_digits=4, decimal_places=1, null=True, blank=True)
    spo2 = models.PositiveSmallIntegerField("اشباع اکسیژن (٪)", null=True, blank=True)
    weight = models.DecimalField("وزن (kg)", max_digits=5, decimal_places=1, null=True, blank=True)
    height = models.DecimalField("قد (cm)", max_digits=5, decimal_places=1, null=True, blank=True)
    waist = models.DecimalField("دور کمر (cm)", max_digits=5, decimal_places=1, null=True, blank=True)
    glucose = models.PositiveSmallIntegerField("قند خون با گلوکومتر (mg/dL)", null=True, blank=True)
    glucose_kind = models.CharField("نوع قند", max_length=8, choices=GlucoseKind.choices, blank=True)

    class Meta:
        ordering = ["-recorded_at", "-id"]
        verbose_name = "علائم حیاتی"
        verbose_name_plural = "علائم حیاتی"
        constraints = [
            models.CheckConstraint(condition=models.Q(spo2__isnull=True) | models.Q(spo2__lte=100), name="vitals_spo2_lte_100"),
            models.CheckConstraint(
                condition=models.Q(systolic__isnull=True) | models.Q(diastolic__isnull=True) | models.Q(systolic__gt=models.F("diastolic")),
                name="vitals_systolic_gt_diastolic",
            ),
        ]

    def __str__(self):
        return f"{self.patient.full_name} {self.recorded_at:%Y-%m-%d}"

    @property
    def bmi(self):
        if not self.weight or not self.height:
            return None
        meters = Decimal(self.height) / 100
        return round(Decimal(self.weight) / (meters * meters), 1)

    @property
    def bp(self):
        return f"{self.systolic}/{self.diastolic}" if self.systolic and self.diastolic else ""

    def clean(self):
        fields = ("systolic", "diastolic", "pulse", "respiratory_rate", "temperature", "spo2", "weight", "height", "glucose")
        if not any(getattr(self, name) is not None for name in fields):
            raise ValidationError("حداقل یکی از مقادیر را وارد کنید.")
        if (self.systolic is None) != (self.diastolic is None):
            raise ValidationError("فشار سیستول و دیاستول را با هم وارد کنید.")
        if self.glucose is not None and not self.glucose_kind:
            raise ValidationError("نوع اندازه‌گیری قند (ناشتا / غیرناشتا) را مشخص کنید.")


class Diagnosis(EncounterChild):
    class Certainty(models.TextChoices):
        CONFIRMED = "confirmed", "قطعی"
        PROVISIONAL = "provisional", "احتمالی"

    encounter = models.ForeignKey(Encounter, on_delete=models.CASCADE, related_name="diagnoses", verbose_name="ویزیت")
    icd = models.ForeignKey(ICD10Code, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name="کد ICD-10")
    text = models.CharField("شرح تشخیص", max_length=300, blank=True)
    is_primary = models.BooleanField("تشخیص اصلی", default=False)
    certainty = models.CharField("قطعیت", max_length=12, choices=Certainty.choices, default=Certainty.CONFIRMED)

    class Meta:
        ordering = ["-is_primary", "id"]
        verbose_name = "تشخیص"
        verbose_name_plural = "تشخیص‌ها"

    def __str__(self):
        return self.label

    @property
    def label(self):
        base = str(self.icd) if self.icd_id else self.text
        if self.icd_id and self.text:
            base = f"{base} — {self.text}"
        return base + (" (احتمالی)" if self.certainty == self.Certainty.PROVISIONAL else "")

    def clean(self):
        if not self.icd_id and not self.text:
            raise ValidationError("کد ICD-10 یا شرح تشخیص را وارد کنید.")


class Problem(models.Model):
    """فهرست مشکلات فعال بیمار؛ بیماری مزمن در برنامه پایش (دیابت، فشار، ...) قرار می‌گیرد."""

    class Status(models.TextChoices):
        ACTIVE = "active", "فعال"
        RESOLVED = "resolved", "برطرف‌شده"

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="problems", verbose_name="بیمار")
    title = models.CharField("عنوان", max_length=200, blank=True)
    icd = models.ForeignKey(ICD10Code, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name="کد ICD-10")
    chronic_program = models.CharField("برنامه پایش بیماری مزمن", max_length=14, choices=ChronicProgram.choices, blank=True)
    onset = models.DateField("تاریخ شروع", null=True, blank=True)
    status = models.CharField("وضعیت", max_length=8, choices=Status.choices, default=Status.ACTIVE)
    note = models.CharField("توضیح", max_length=300, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name="ثبت‌کننده")
    created_at = models.DateTimeField("زمان ثبت", auto_now_add=True)
    updated_at = models.DateTimeField("آخرین تغییر", auto_now=True)

    class Meta:
        ordering = ["status", "-created_at"]
        verbose_name = "مشکل / بیماری"
        verbose_name_plural = "فهرست مشکلات بیماران"

    def __str__(self):
        return self.label

    @property
    def label(self):
        return self.title or str(self.icd)

    def clean(self):
        if not self.icd_id and not self.title:
            raise ValidationError("عنوان یا کد ICD-10 را وارد کنید.")


class Allergy(models.Model):
    class Kind(models.TextChoices):
        DRUG = "drug", "دارویی"
        FOOD = "food", "غذایی"
        OTHER = "other", "سایر (لاتکس، گرده، ...)"

    class Severity(models.TextChoices):
        MILD = "mild", "خفیف"
        MODERATE = "moderate", "متوسط"
        SEVERE = "severe", "شدید / آنافیلاکسی"

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="allergies", verbose_name="بیمار")
    kind = models.CharField("نوع", max_length=6, choices=Kind.choices, default=Kind.DRUG)
    substance = models.CharField(
        "ماده حساسیت‌زا", max_length=150,
        help_text="برای دارو نام ژنریک را بنویسید (مثلاً Penicillin) تا هنگام نسخه‌نویسی هشدار داده شود.",
    )
    reaction = models.CharField("واکنش", max_length=200, blank=True)
    severity = models.CharField("شدت", max_length=8, choices=Severity.choices, default=Severity.MODERATE)
    is_active = models.BooleanField("فعال", default=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name="ثبت‌کننده")
    created_at = models.DateTimeField("زمان ثبت", auto_now_add=True)

    class Meta:
        ordering = ["-is_active", "substance"]
        verbose_name = "حساسیت"
        verbose_name_plural = "حساسیت‌ها"

    def __str__(self):
        return f"{self.substance} ({self.get_severity_display()})"


class HistoryEntry(models.Model):
    class Kind(models.TextChoices):
        MEDICAL = "medical", "سابقه بیماری"
        SURGICAL = "surgical", "سابقه جراحی / بستری"
        FAMILY = "family", "سابقه خانوادگی"
        SOCIAL = "social", "عادات (دخانیات، الکل، ورزش، شغل)"
        MEDICATION = "medication", "داروهای مصرفی خارج از مجموعه"
        IMMUNIZATION = "immunization", "واکسیناسیون"
        OBSTETRIC = "obstetric", "سابقه بارداری و زایمان"

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="history", verbose_name="بیمار")
    kind = models.CharField("نوع", max_length=12, choices=Kind.choices)
    text = models.CharField("شرح", max_length=300)
    date = models.DateField("تاریخ", null=True, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name="ثبت‌کننده")
    created_at = models.DateTimeField("زمان ثبت", auto_now_add=True)

    class Meta:
        ordering = ["kind", "-date", "-id"]
        verbose_name = "سابقه"
        verbose_name_plural = "سوابق پزشکی"

    def __str__(self):
        return f"{self.get_kind_display()}: {self.text}"


class Referral(models.Model):
    """ارجاع به پزشک یا خدمت دیگر؛ پزشک مقصد با ارجاع به پرونده دسترسی درمانی پیدا می‌کند."""

    class Status(models.TextChoices):
        OPEN = "open", "در انتظار"
        DONE = "done", "انجام شد"
        CANCELLED = "cancelled", "لغو شد"

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="referrals", verbose_name="بیمار")
    encounter = models.ForeignKey(Encounter, on_delete=models.PROTECT, null=True, blank=True, related_name="referrals", verbose_name="ویزیت")
    from_provider = models.ForeignKey(Doctor, on_delete=models.PROTECT, related_name="referrals_sent", verbose_name="ارجاع‌دهنده")
    to_provider = models.ForeignKey(
        Doctor, on_delete=models.PROTECT, null=True, blank=True, related_name="referrals_received", verbose_name="پزشک / درمانگر مقصد"
    )
    to_service = models.CharField("خدمت / مرکز مقصد", max_length=150, blank=True, help_text="اگر مقصد بیرون از مجموعه است")
    reason = models.CharField("علت ارجاع", max_length=300)
    status = models.CharField("وضعیت", max_length=10, choices=Status.choices, default=Status.OPEN)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name="ایجادکننده")
    created_at = models.DateTimeField("زمان", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "ارجاع"
        verbose_name_plural = "ارجاع‌ها"

    def __str__(self):
        return f"{self.patient.full_name} → {self.to_provider or self.to_service}"

    def clean(self):
        if not self.to_provider_id and not self.to_service:
            raise ValidationError("پزشک مقصد یا خدمت مقصد را مشخص کنید.")

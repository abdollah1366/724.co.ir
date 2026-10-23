"""پرونده الکترونیک سلامت: شاخص واحد بیمار برای همه بخش‌های ۷۲۴ و ثبت هر بار مشاهده پرونده.

ویزیت، نوبت، پذیرش آزمایشگاه و فاکتور داروخانه هر کدام به یک «بیمار» وصل می‌شوند تا پزشک معالج همه سوابق را
یک‌جا ببیند. هر بار باز شدن پرونده بالینی در RecordAccess ثبت می‌شود (ماده ۶۴۸ ق.م.ا و منشور حقوق بیمار).
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.audit.documents import ImmutableRecord, next_number
from apps.core.validators import validate_mobile, validate_national_code


class Sex(models.TextChoices):
    MALE = "m", "مرد"
    FEMALE = "f", "زن"


class BloodGroup(models.TextChoices):
    A_POS = "A+", "A+"
    A_NEG = "A-", "A-"
    B_POS = "B+", "B+"
    B_NEG = "B-", "B-"
    AB_POS = "AB+", "AB+"
    AB_NEG = "AB-", "AB-"
    O_POS = "O+", "O+"
    O_NEG = "O-", "O-"


class Patient(models.Model):
    """یک نفر = یک پرونده. شناسه: کد ملی یا برای اتباع، شماره گذرنامه/کد اتباع."""

    file_number = models.CharField("شماره پرونده", max_length=24, unique=True, editable=False)
    national_code = models.CharField(
        "کد ملی", max_length=10, unique=True, null=True, blank=True, validators=[validate_national_code]
    )
    foreign_id = models.CharField("شماره گذرنامه / کد اتباع", max_length=30, unique=True, null=True, blank=True)
    first_name = models.CharField("نام", max_length=80, blank=True)
    last_name = models.CharField("نام خانوادگی", max_length=80, blank=True)
    father_name = models.CharField("نام پدر", max_length=80, blank=True)
    sex = models.CharField("جنس", max_length=1, choices=Sex.choices, blank=True)
    birth_date = models.DateField("تاریخ تولد", null=True, blank=True)
    mobile = models.CharField("موبایل", max_length=11, blank=True, validators=[validate_mobile])
    phone = models.CharField("تلفن ثابت", max_length=20, blank=True)
    address = models.CharField("آدرس", max_length=300, blank=True)
    blood_group = models.CharField("گروه خونی", max_length=3, choices=BloodGroup.choices, blank=True)
    emergency_contact = models.CharField("همراه / تماس اضطراری", max_length=150, blank=True)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="ehr_patient",
        verbose_name="حساب کاربری", help_text="فقط پس از تأیید کد پیامکی به موبایل ثبت‌شده متصل می‌شود.",
    )
    reminder_consent_at = models.DateTimeField(
        "رضایت به پیامک یادآوری", null=True, blank=True,
        help_text="فقط با رضایت بیمار پیامک یادآوری ویزیت و دارو ارسال می‌شود؛ متن پیامک هیچ اطلاعات پزشکی ندارد.",
    )
    is_verified = models.BooleanField(
        "هویت در پذیرش تأیید شده", default=False,
        help_text="پرونده‌ای که از نوبت اینترنتی یا فروش ساخته شده تا تطبیق با کارت ملی در پذیرش «تأییدنشده» است.",
    )
    created_at = models.DateTimeField("زمان ایجاد", auto_now_add=True)
    updated_at = models.DateTimeField("آخرین تغییر", auto_now=True)

    class Meta:
        ordering = ["last_name", "first_name"]
        verbose_name = "بیمار"
        verbose_name_plural = "بیماران (پرونده سلامت)"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(national_code__isnull=False) | models.Q(foreign_id__isnull=False),
                name="ehr_patient_has_identifier",
            )
        ]
        permissions = [
            ("view_clinical_record", "مشاهده پرونده بالینی بیمارانی که با آن‌ها رابطه درمانی دارد"),
            ("view_all_records", "مشاهده پرونده بالینی همه بیماران بدون ذکر دلیل (مسئول فنی)"),
        ]

    def __str__(self):
        return f"{self.full_name} ({self.file_number})"

    def save(self, *args, **kwargs):
        if not self.file_number:
            self.file_number = next_number("P")
        super().save(*args, **kwargs)

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip() or "بدون نام"

    @property
    def identifier(self):
        return self.national_code or self.foreign_id

    @property
    def masked_identifier(self):
        value = self.identifier or ""
        return value[:3] + "****" + value[-3:] if len(value) > 6 else "****"

    def age_years(self, on=None):
        if not self.birth_date:
            return None
        on = on or timezone.localdate()
        years = on.year - self.birth_date.year
        if (on.month, on.day) < (self.birth_date.month, self.birth_date.day):
            years -= 1
        return years


class RecordAccess(ImmutableRecord):
    """هر بار باز شدن پرونده بالینی. قابل ویرایش و حذف نیست."""

    class Kind(models.TextChoices):
        CARE = "care", "رابطه درمانی"
        ALL = "all", "مجوز مشاهده همه پرونده‌ها"
        EMERGENCY = "emergency", "دسترسی با ذکر دلیل"

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="accesses", verbose_name="بیمار")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name="کاربر")
    kind = models.CharField("نوع دسترسی", max_length=10, choices=Kind.choices)
    reason = models.CharField("دلیل", max_length=300, blank=True)
    ip = models.CharField("IP", max_length=45, blank=True)
    created_at = models.DateTimeField("زمان", auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "مشاهده پرونده"
        verbose_name_plural = "گزارش مشاهده پرونده‌ها"
        indexes = [models.Index(fields=["patient", "user", "created_at"])]

    def __str__(self):
        return f"{self.user} → {self.patient} ({self.get_kind_display()})"

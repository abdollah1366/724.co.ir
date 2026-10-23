"""پرونده بیمار آزمایشگاه، قراردادهای بیمه و نوبت نمونه‌گیری."""

import datetime
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.accounting.models import Party
from apps.clinic.models import WEEKDAY_CHOICES
from apps.core.validators import validate_mobile, validate_national_code

from .catalog import LabTest, Sex

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def random_code(length=8):
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))


class InsurancePlan(models.Model):
    """قرارداد آزمایشگاه با یک بیمه‌گر. بیمه پایه سهم خود را از تعرفه می‌پردازد و بیمه تکمیلی از سهم بیمار."""

    class Kind(models.TextChoices):
        BASIC = "basic", "بیمه پایه"
        SUPPLEMENTARY = "supplementary", "بیمه تکمیلی"

    insurer = models.ForeignKey(
        Party, on_delete=models.PROTECT, related_name="lab_plans", verbose_name="بیمه‌گر (طرف حساب)",
        limit_choices_to={"kind": Party.Kind.INSURER},
    )
    name = models.CharField("نام قرارداد", max_length=150)
    kind = models.CharField("نوع", max_length=14, choices=Kind.choices)
    contract_no = models.CharField("شماره قرارداد", max_length=60, blank=True)
    start_date = models.DateField("شروع قرارداد")
    end_date = models.DateField("پایان قرارداد", null=True, blank=True)
    tariff_percent = models.PositiveSmallIntegerField(
        "تعرفه قرارداد (٪ تعرفه آزاد)", default=100,
        help_text="بیمه پایه معمولاً تعرفه دولتی؛ مثلاً ۶۰ یعنی تعرفه قرارداد ۶۰٪ تعرفه آزاد است. فقط برای بیمه پایه.",
    )
    coverage_percent = models.PositiveSmallIntegerField(
        "درصد پوشش", default=70, help_text="پایه: درصد تعرفه قرارداد؛ تکمیلی: درصد سهم بیمار (فرانشیز)"
    )
    max_per_order = models.PositiveBigIntegerField("سقف تعهد هر پذیرش (ریال)", null=True, blank=True)
    requires_approval_code = models.BooleanField("کد تأیید / کد رهگیری برای هر پذیرش الزامی است", default=False)
    covers_home_sampling = models.BooleanField("هزینه نمونه‌گیری در منزل را پوشش می‌دهد", default=False)
    notes = models.TextField("شرایط قرارداد", blank=True)
    is_active = models.BooleanField("فعال", default=True)

    class Meta:
        ordering = ["kind", "name"]
        verbose_name = "قرارداد بیمه"
        verbose_name_plural = "قراردادهای بیمه آزمایشگاه"
        constraints = [
            models.CheckConstraint(condition=models.Q(coverage_percent__lte=100), name="lab_plan_coverage_lte_100"),
            models.CheckConstraint(condition=models.Q(tariff_percent__lte=100), name="lab_plan_tariff_lte_100"),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_kind_display()})"

    def is_valid_on(self, date):
        return self.is_active and self.start_date <= date and (self.end_date is None or date <= self.end_date)


class PlanTestRule(models.Model):
    """استثنای قرارداد برای یک آزمایش: عدم پوشش، تعرفه توافقی، درصد متفاوت یا نیاز به تأییدیه."""

    plan = models.ForeignKey(InsurancePlan, on_delete=models.CASCADE, related_name="rules", verbose_name="قرارداد")
    test = models.ForeignKey(LabTest, on_delete=models.CASCADE, related_name="plan_rules", verbose_name="آزمایش")
    is_covered = models.BooleanField("تحت پوشش", default=True)
    price = models.PositiveBigIntegerField("تعرفه توافقی (ریال)", null=True, blank=True)
    coverage_percent = models.PositiveSmallIntegerField("درصد پوشش", null=True, blank=True)
    requires_approval = models.BooleanField("نیاز به تأییدیه بیمه", default=False)

    class Meta:
        verbose_name = "تعهد آزمایش در قرارداد"
        verbose_name_plural = "تعهدات آزمایش‌ها در قرارداد"
        constraints = [models.UniqueConstraint(fields=["plan", "test"], name="unique_plan_test_rule")]

    def __str__(self):
        return f"{self.plan} - {self.test}"


class LabPatient(models.Model):
    national_code = models.CharField(
        "کد ملی", max_length=10, unique=True, null=True, blank=True, validators=[validate_national_code]
    )
    foreign_id = models.CharField("شماره گذرنامه / کد اتباع", max_length=30, unique=True, null=True, blank=True)
    first_name = models.CharField("نام", max_length=80)
    last_name = models.CharField("نام خانوادگی", max_length=80)
    father_name = models.CharField("نام پدر", max_length=80, blank=True)
    sex = models.CharField("جنس", max_length=1, choices=[(Sex.MALE, "مرد"), (Sex.FEMALE, "زن")])
    birth_date = models.DateField("تاریخ تولد")
    mobile = models.CharField("موبایل", max_length=11, validators=[validate_mobile])
    phone = models.CharField("تلفن ثابت", max_length=20, blank=True)
    address = models.CharField("آدرس", max_length=300, blank=True)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="lab_patient",
        verbose_name="حساب کاربری", help_text="فقط پس از تأیید کد پیامکی به موبایل ثبت‌شده متصل می‌شود.",
    )
    basic_plan = models.ForeignKey(
        InsurancePlan, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name="بیمه پایه",
        limit_choices_to={"kind": InsurancePlan.Kind.BASIC},
    )
    basic_insurance_no = models.CharField("شماره بیمه پایه", max_length=30, blank=True)
    supplementary_plan = models.ForeignKey(
        InsurancePlan, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name="بیمه تکمیلی",
        limit_choices_to={"kind": InsurancePlan.Kind.SUPPLEMENTARY},
    )
    supplementary_insurance_no = models.CharField("شماره بیمه تکمیلی", max_length=30, blank=True)
    patient = models.OneToOneField(
        "ehr.Patient", on_delete=models.PROTECT, null=True, blank=True, related_name="lab_patient",
        verbose_name="پرونده سلامت", help_text="با کد ملی یا کد اتباع خودکار وصل می‌شود.",
    )
    reminder_consent_at = models.DateTimeField(
        "رضایت به یادآوری آزمایش دوره‌ای", null=True, blank=True, help_text="خالی = پیامک یادآوری ارسال نمی‌شود"
    )
    created_at = models.DateTimeField("زمان ایجاد", auto_now_add=True)
    updated_at = models.DateTimeField("آخرین تغییر", auto_now=True)

    class Meta:
        ordering = ["last_name", "first_name"]
        verbose_name = "بیمار آزمایشگاه"
        verbose_name_plural = "بیماران آزمایشگاه"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(national_code__isnull=False) | models.Q(foreign_id__isnull=False),
                name="lab_patient_has_identifier",
            )
        ]

    def __str__(self):
        return self.full_name

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def identifier(self):
        return self.national_code or self.foreign_id

    @property
    def masked_identifier(self):
        value = self.identifier or ""
        return value[:3] + "****" + value[-3:] if len(value) > 6 else "****"

    def age_days(self, on=None):
        return ((on or timezone.localdate()) - self.birth_date).days

    def age_years(self, on=None):
        on = on or timezone.localdate()
        years = on.year - self.birth_date.year
        if (on.month, on.day) < (self.birth_date.month, self.birth_date.day):
            years -= 1
        return years


class SamplingSchedule(models.Model):
    class Kind(models.TextChoices):
        LAB = "lab", "نمونه‌گیری در آزمایشگاه"
        HOME = "home", "نمونه‌گیری در منزل"

    kind = models.CharField("نوع", max_length=4, choices=Kind.choices, default=Kind.LAB)
    weekday = models.PositiveSmallIntegerField("روز هفته", choices=WEEKDAY_CHOICES)
    start_time = models.TimeField("از ساعت")
    end_time = models.TimeField("تا ساعت")
    slot_minutes = models.PositiveSmallIntegerField("طول هر بازه (دقیقه)", default=15)
    capacity = models.PositiveSmallIntegerField("ظرفیت هر بازه (نفر)", default=3)
    is_active = models.BooleanField("فعال", default=True)

    class Meta:
        ordering = ["kind", "weekday", "start_time"]
        verbose_name = "برنامه نمونه‌گیری"
        verbose_name_plural = "برنامه نمونه‌گیری"
        constraints = [
            models.CheckConstraint(condition=models.Q(end_time__gt=models.F("start_time")), name="sampling_end_after_start"),
            models.CheckConstraint(condition=models.Q(slot_minutes__gte=5), name="sampling_slot_min_5"),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} {self.get_weekday_display()} {self.start_time:%H:%M}-{self.end_time:%H:%M}"

    def slot_times(self):
        base = datetime.date(2000, 1, 1)
        current = datetime.datetime.combine(base, self.start_time)
        end = datetime.datetime.combine(base, self.end_time)
        step = datetime.timedelta(minutes=self.slot_minutes)
        times = []
        while current + step <= end:
            times.append(current.time())
            current += step
        return times


class SamplingAppointmentQuerySet(models.QuerySet):
    def active(self):
        return self.exclude(status__in=[SamplingAppointment.Status.CANCELLED, SamplingAppointment.Status.NO_SHOW])


class SamplingAppointment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "ثبت‌شده"
        CONFIRMED = "confirmed", "تأییدشده"
        ARRIVED = "arrived", "پذیرش شد"
        CANCELLED = "cancelled", "لغوشده"
        NO_SHOW = "no_show", "مراجعه نکرد"

    kind = models.CharField("نوع", max_length=4, choices=SamplingSchedule.Kind.choices, default=SamplingSchedule.Kind.LAB)
    date = models.DateField("تاریخ")
    time = models.TimeField("ساعت")
    full_name = models.CharField("نام و نام خانوادگی", max_length=150)
    national_code = models.CharField("کد ملی", max_length=10, validators=[validate_national_code])
    mobile = models.CharField("موبایل", max_length=11, validators=[validate_mobile])
    address = models.CharField("آدرس نمونه‌گیری در منزل", max_length=300, blank=True)
    tests = models.ManyToManyField(LabTest, blank=True, related_name="+", verbose_name="آزمایش‌های درخواستی")
    note = models.CharField("توضیح برای پذیرش", max_length=300, blank=True, help_text="اطلاعات بالینی وارد نکنید")
    status = models.CharField("وضعیت", max_length=10, choices=Status.choices, default=Status.PENDING)
    tracking_code = models.CharField("کد پیگیری", max_length=8, unique=True, default=random_code, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="lab_appointments",
        verbose_name="کاربر",
    )
    consent_at = models.DateTimeField("زمان رضایت به پردازش اطلاعات")
    reminder_sent_at = models.DateTimeField("ارسال یادآوری", null=True, blank=True, editable=False)
    created_at = models.DateTimeField("زمان ثبت", auto_now_add=True)

    objects = SamplingAppointmentQuerySet.as_manager()

    class Meta:
        ordering = ["date", "time"]
        verbose_name = "نوبت نمونه‌گیری"
        verbose_name_plural = "نوبت‌های نمونه‌گیری"
        indexes = [models.Index(fields=["kind", "date", "time"])]

    def __str__(self):
        return f"{self.full_name} - {self.date} {self.time:%H:%M}"

    @property
    def is_cancellable(self):
        start = timezone.make_aware(datetime.datetime.combine(self.date, self.time))
        return self.status in (self.Status.PENDING, self.Status.CONFIRMED) and start > timezone.now()

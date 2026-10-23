"""سامانه اطلاعات آزمایشگاه (LIS): نمونه و بارکد، جواب، نسخه‌های اصلاحی، مقادیر بحرانی، کنترل کیفی و دستگاه‌ها."""

import hashlib
import secrets

from django.conf import settings
from django.db import models

from apps.audit.documents import ImmutableRecord

from .catalog import LabSection, LabTest, SpecimenType
from .orders import LabOrder, LabOrderItem


class Specimen(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "در انتظار نمونه‌گیری"
        COLLECTED = "collected", "نمونه‌گیری شد"
        RECEIVED = "received", "در بخش دریافت شد"
        REJECTED = "rejected", "مردود"

    order = models.ForeignKey(LabOrder, on_delete=models.PROTECT, related_name="specimens", verbose_name="پذیرش")
    specimen_type = models.ForeignKey(SpecimenType, on_delete=models.PROTECT, related_name="+", verbose_name="نوع نمونه")
    barcode = models.CharField("بارکد", max_length=24, unique=True)
    status = models.CharField("وضعیت", max_length=10, choices=Status.choices, default=Status.PENDING)
    collected_at = models.DateTimeField("زمان نمونه‌گیری", null=True, blank=True)
    collected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name="نمونه‌گیر"
    )
    received_at = models.DateTimeField("زمان دریافت در بخش", null=True, blank=True)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name="دریافت‌کننده"
    )
    rejection_reason = models.CharField("علت مردودی", max_length=200, blank=True)
    recollect_of = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="recollections", verbose_name="نمونه‌گیری مجدد برای"
    )

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "نمونه"
        verbose_name_plural = "نمونه‌ها"

    def __str__(self):
        return f"{self.barcode} ({self.specimen_type})"


class LabResult(models.Model):
    """جواب یک آزمایش. پس از تأیید مسئول فنی قفل است؛ اصلاح فقط با ثبت نسخه جدید و علت ممکن است."""

    class Status(models.TextChoices):
        PENDING = "pending", "در انتظار جواب"
        ENTERED = "entered", "ثبت‌شده، منتظر تأیید"
        APPROVED = "approved", "تأییدشده"

    class Flag(models.TextChoices):
        NORMAL = "", "طبیعی"
        LOW = "L", "پایین"
        HIGH = "H", "بالا"
        CRITICAL_LOW = "LL", "بحرانی پایین"
        CRITICAL_HIGH = "HH", "بحرانی بالا"
        ABNORMAL = "A", "غیرطبیعی"

    class Source(models.TextChoices):
        MANUAL = "manual", "ثبت دستی"
        INSTRUMENT = "instrument", "دستگاه"

    item = models.OneToOneField(LabOrderItem, on_delete=models.PROTECT, related_name="result", verbose_name="آزمایش پذیرش")
    specimen = models.ForeignKey(Specimen, on_delete=models.PROTECT, null=True, blank=True, related_name="results", verbose_name="نمونه")
    status = models.CharField("وضعیت", max_length=10, choices=Status.choices, default=Status.PENDING)
    value = models.CharField("جواب", max_length=500, blank=True)
    numeric_value = models.DecimalField("مقدار عددی", max_digits=14, decimal_places=4, null=True, blank=True)
    unit = models.CharField("واحد", max_length=30, blank=True)
    reference_text = models.CharField("محدوده مرجع", max_length=120, blank=True)
    flag = models.CharField("علامت", max_length=2, choices=Flag.choices, blank=True)
    is_critical = models.BooleanField("بحرانی", default=False)
    delta_alert = models.CharField("هشدار تغییر نسبت به جواب قبلی", max_length=120, blank=True)
    comment = models.CharField("توضیح آزمایشگاه", max_length=500, blank=True)
    source = models.CharField("منبع", max_length=10, choices=Source.choices, default=Source.MANUAL)
    instrument = models.ForeignKey("Instrument", on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name="دستگاه")
    entered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name="ثبت‌کننده"
    )
    entered_at = models.DateTimeField("زمان ثبت", null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name="تأییدکننده"
    )
    approved_at = models.DateTimeField("زمان تأیید", null=True, blank=True)
    version = models.PositiveSmallIntegerField("نسخه", default=1)

    class Meta:
        ordering = ["item__order", "item__test__section__order", "item__test__order"]
        verbose_name = "جواب آزمایش"
        verbose_name_plural = "جواب‌های آزمایش"
        permissions = [
            ("enter_labresult", "ثبت جواب آزمایش"),
            ("approve_labresult", "تأیید نهایی جواب (مسئول فنی)"),
            ("amend_labresult", "اصلاح جواب تأییدشده"),
        ]

    def __str__(self):
        return f"{self.item.test.code}: {self.value}"

    @property
    def test(self):
        return self.item.test

    @property
    def is_amended(self):
        return self.version > 1

    def save(self, *args, **kwargs):
        system = self.__dict__.pop("_system_write", False)  # مجوز فقط برای همین یک ذخیره
        if self.pk and not system:
            stored = type(self).objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if stored == self.Status.APPROVED:
                raise PermissionError("جواب تأییدشده فقط با «اصلاح جواب» و ثبت علت قابل تغییر است.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("جواب آزمایش قابل حذف نیست.")


class ResultRevision(ImmutableRecord):
    result = models.ForeignKey(LabResult, on_delete=models.PROTECT, related_name="revisions", verbose_name="جواب")
    version = models.PositiveSmallIntegerField("نسخه قبلی")
    old_value = models.CharField("جواب قبلی", max_length=500)
    old_flag = models.CharField("علامت قبلی", max_length=2, blank=True)
    old_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name="تأییدکننده قبلی"
    )
    old_approved_at = models.DateTimeField("زمان تأیید قبلی", null=True, blank=True)
    reason = models.CharField("علت اصلاح", max_length=300)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name="کاربر")
    created_at = models.DateTimeField("زمان", auto_now_add=True)

    class Meta:
        ordering = ["result", "version"]
        verbose_name = "تاریخچه اصلاح جواب"
        verbose_name_plural = "تاریخچه اصلاح جواب‌ها"


class CriticalNotification(ImmutableRecord):
    """گزارش اطلاع‌رسانی مقدار بحرانی به بیمار یا پزشک (چه کسی، به چه کسی، چه زمانی، با تکرار شفاهی)."""

    class Recipient(models.TextChoices):
        PATIENT = "patient", "بیمار"
        DOCTOR = "doctor", "پزشک معالج"
        OTHER = "other", "سایر (همراه / مرکز درمانی)"

    result = models.ForeignKey(LabResult, on_delete=models.PROTECT, related_name="critical_notifications", verbose_name="جواب")
    recipient = models.CharField("گیرنده", max_length=8, choices=Recipient.choices)
    recipient_name = models.CharField("نام گیرنده", max_length=120)
    phone = models.CharField("تلفن", max_length=20)
    read_back = models.BooleanField("گیرنده مقدار را تکرار کرد", default=False)
    note = models.CharField("توضیح", max_length=300, blank=True)
    notified_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name="اطلاع‌دهنده")
    notified_at = models.DateTimeField("زمان", auto_now_add=True)

    class Meta:
        ordering = ["-notified_at"]
        verbose_name = "اطلاع‌رسانی مقدار بحرانی"
        verbose_name_plural = "اطلاع‌رسانی مقادیر بحرانی"


class QCMaterial(models.Model):
    """ماده کنترل (کنترل نرمال/پاتولوژیک) با میانگین و انحراف معیار هدف برای یک آزمایش."""

    test = models.ForeignKey(LabTest, on_delete=models.PROTECT, related_name="qc_materials", verbose_name="آزمایش")
    name = models.CharField("نام کنترل", max_length=120)
    lot = models.CharField("سری ساخت (لات)", max_length=40)
    level = models.CharField("سطح", max_length=20, default="نرمال")
    mean = models.DecimalField("میانگین هدف", max_digits=12, decimal_places=4)
    sd = models.DecimalField("انحراف معیار (SD)", max_digits=12, decimal_places=4)
    expiry = models.DateField("تاریخ انقضا")
    is_active = models.BooleanField("فعال", default=True)

    class Meta:
        ordering = ["test", "level"]
        verbose_name = "ماده کنترل کیفی"
        verbose_name_plural = "مواد کنترل کیفی"
        constraints = [models.CheckConstraint(condition=models.Q(sd__gt=0), name="qc_sd_positive")]

    def __str__(self):
        return f"{self.test.code} {self.level} ({self.lot})"


class QCResult(ImmutableRecord):
    class Status(models.TextChoices):
        ACCEPTED = "accepted", "قابل قبول"
        WARNING = "warning", "هشدار"
        REJECTED = "rejected", "مردود"

    material = models.ForeignKey(QCMaterial, on_delete=models.PROTECT, related_name="results", verbose_name="ماده کنترل")
    value = models.DecimalField("مقدار", max_digits=12, decimal_places=4)
    z_score = models.DecimalField("Z", max_digits=8, decimal_places=2)
    status = models.CharField("وضعیت", max_length=8, choices=Status.choices)
    violations = models.CharField("قواعد نقض‌شده", max_length=100, blank=True)
    corrective_action = models.CharField("اقدام اصلاحی", max_length=300, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name="کاربر")
    run_at = models.DateTimeField("زمان اجرا", auto_now_add=True)

    class Meta:
        ordering = ["-run_at", "-id"]
        verbose_name = "نتیجه کنترل کیفی"
        verbose_name_plural = "نتایج کنترل کیفی"


class Instrument(models.Model):
    """دستگاه یا نرم‌افزار واسط که جواب‌ها را با کلید اختصاصی از طریق API ارسال می‌کند."""

    name = models.CharField("نام دستگاه", max_length=120)
    code = models.CharField("کد", max_length=20, unique=True)
    section = models.ForeignKey(LabSection, on_delete=models.PROTECT, related_name="instruments", verbose_name="بخش")
    api_key_hash = models.CharField("هش کلید API", max_length=64, blank=True, editable=False)
    is_active = models.BooleanField("فعال", default=True)
    last_seen_at = models.DateTimeField("آخرین ارسال", null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "دستگاه / واسط"
        verbose_name_plural = "دستگاه‌ها و واسط‌ها"

    def __str__(self):
        return self.name

    @staticmethod
    def hash_key(key):
        return hashlib.sha256(key.encode()).hexdigest()

    def set_api_key(self):
        key = secrets.token_urlsafe(32)
        self.api_key_hash = self.hash_key(key)
        self.save(update_fields=["api_key_hash"])
        return key

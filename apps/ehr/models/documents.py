"""درخواست آزمایش از ویزیت و مدارک بیرونی (اسکن، عکس یا PDF گزارش‌های مراکز دیگر).

فایل‌های پزشکی در پوشه خصوصی ذخیره می‌شوند که مستقیم از وب در دسترس نیست و فقط از نمای دارای مجوز باز می‌شوند.
نام فایل تصادفی است تا هیچ اطلاعات بیمار در نام فایل یا آدرس نباشد.
"""

import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import FileSystemStorage
from django.db import models
from django.utils import timezone
from django.utils.functional import LazyObject

from apps.audit.documents import next_number
from apps.clinic.models import Doctor

from .patients import Patient

ALLOWED_EXTENSIONS = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                      ".webp": "image/webp"}
MAGIC = {".pdf": (b"%PDF",), ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",), ".png": (b"\x89PNG",),
         ".webp": (b"RIFF",)}
MAX_UPLOAD_MB = 15


class PrivateStorage(LazyObject):
    def _setup(self):
        self._wrapped = FileSystemStorage(location=settings.PRIVATE_MEDIA_ROOT, base_url=None)


private_storage = PrivateStorage()


def get_private_storage():
    return private_storage


def _upload_to(instance, filename):
    ext = Path(filename).suffix.lower()
    return f"ehr/{timezone.now():%Y/%m}/{uuid.uuid4().hex}{ext}"


def validate_medical_file(file):
    ext = Path(file.name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValidationError("فقط فایل PDF یا تصویر (JPG، PNG، WEBP) مجاز است.")
    if file.size > MAX_UPLOAD_MB * 1024 * 1024:
        raise ValidationError(f"حجم هر فایل حداکثر {MAX_UPLOAD_MB} مگابایت است.")
    head = file.read(8)
    file.seek(0)
    if not any(head.startswith(sig) for sig in MAGIC[ext]):
        raise ValidationError("محتوای فایل با پسوند آن یکی نیست.")


class LabRequest(models.Model):
    """درخواست آزمایش پزشک از ویزیت؛ پذیرش آزمایشگاه آن را به پذیرش آزمایشگاه تبدیل می‌کند."""

    class Status(models.TextChoices):
        OPEN = "open", "در انتظار مراجعه به آزمایشگاه"
        RECEIVED = "received", "پذیرش شد"
        CANCELLED = "cancelled", "لغو شد"

    number = models.CharField("شماره درخواست", max_length=24, unique=True, editable=False)
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="lab_requests", verbose_name="بیمار")
    encounter = models.ForeignKey("ehr.Encounter", on_delete=models.PROTECT, null=True, blank=True, related_name="lab_requests",
                                  verbose_name="ویزیت")
    doctor = models.ForeignKey(Doctor, on_delete=models.PROTECT, related_name="lab_requests", verbose_name="پزشک درخواست‌کننده")
    tests = models.ManyToManyField("lab.LabTest", blank=True, related_name="+", verbose_name="آزمایش‌ها")
    panels = models.ManyToManyField("lab.LabPanel", blank=True, related_name="+", verbose_name="بسته‌ها")
    is_urgent = models.BooleanField("فوری", default=False)
    clinical_note = models.CharField("توضیح بالینی برای آزمایشگاه", max_length=300, blank=True)
    status = models.CharField("وضعیت", max_length=10, choices=Status.choices, default=Status.OPEN)
    lab_order = models.OneToOneField("lab.LabOrder", on_delete=models.PROTECT, null=True, blank=True, related_name="ehr_request",
                                     verbose_name="پذیرش آزمایشگاه")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name="ایجادکننده")
    created_at = models.DateTimeField("زمان", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "درخواست آزمایش"
        verbose_name_plural = "درخواست‌های آزمایش"

    def __str__(self):
        return self.number

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = next_number("LR")
        super().save(*args, **kwargs)


class ExternalDocument(models.Model):
    class Kind(models.TextChoices):
        LAB = "lab", "جواب آزمایش بیرون"
        RADIOLOGY = "radiology", "رادیولوژی / سی‌تی / ام‌آر‌آی"
        ULTRASOUND = "ultrasound", "سونوگرافی"
        ECG = "ecg", "نوار قلب / اکو / تست ورزش"
        PATHOLOGY = "pathology", "پاتولوژی"
        DISCHARGE = "discharge", "خلاصه پرونده بیمارستان"
        LETTER = "letter", "نامه ارجاع / گزارش پزشک"
        AUDIOGRAM = "audiogram", "ادیوگرام / شنوایی‌سنجی"
        CONSENT = "consent", "رضایت‌نامه امضاشده"
        OTHER = "other", "سایر"

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="documents", verbose_name="بیمار")
    encounter = models.ForeignKey("ehr.Encounter", on_delete=models.PROTECT, null=True, blank=True, related_name="documents",
                                  verbose_name="ویزیت")
    kind = models.CharField("نوع مدرک", max_length=12, choices=Kind.choices)
    title = models.CharField("عنوان", max_length=200)
    source = models.CharField("مرکز صادرکننده", max_length=150, blank=True)
    document_date = models.DateField("تاریخ مدرک")
    note = models.TextField("خلاصه / تفسیر", blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name="بارگذاری توسط")
    created_at = models.DateTimeField("زمان بارگذاری", auto_now_add=True)
    is_void = models.BooleanField("اشتباه ثبت شده", default=False, editable=False)
    void_reason = models.CharField("علت ابطال", max_length=300, blank=True, editable=False)

    class Meta:
        ordering = ["-document_date", "-id"]
        verbose_name = "مدرک بیرونی"
        verbose_name_plural = "مدارک بیرونی (اسکن و تصویر)"

    def __str__(self):
        return f"{self.get_kind_display()}: {self.title}"

    def delete(self, *args, **kwargs):
        raise PermissionError("مدرک پزشکی حذف نمی‌شود؛ آن را «اشتباه ثبت شده» علامت بزنید.")


class DocumentFile(models.Model):
    document = models.ForeignKey(ExternalDocument, on_delete=models.PROTECT, related_name="files", verbose_name="مدرک")
    file = models.FileField("فایل", storage=get_private_storage, upload_to=_upload_to, validators=[validate_medical_file])
    content_type = models.CharField("نوع فایل", max_length=40, editable=False)
    size = models.PositiveIntegerField("حجم (بایت)", editable=False)
    page = models.PositiveSmallIntegerField("صفحه", default=1)

    class Meta:
        ordering = ["document", "page"]
        verbose_name = "فایل مدرک"
        verbose_name_plural = "فایل‌های مدارک"

    def save(self, *args, **kwargs):
        ext = Path(self.file.name).suffix.lower()
        self.content_type = ALLOWED_EXTENSIONS.get(ext, "application/octet-stream")
        self.size = self.file.size
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("فایل مدرک پزشکی حذف نمی‌شود.")

    @property
    def is_image(self):
        return self.content_type.startswith("image/")


class ExternalLabValue(models.Model):
    """مقدار عددی آزمایش بیرون (مثلاً HbA1c) که از روی برگه وارد می‌شود تا در نمودار روند بیاید."""

    document = models.ForeignKey(ExternalDocument, on_delete=models.PROTECT, related_name="values", verbose_name="مدرک")
    test = models.ForeignKey("lab.LabTest", on_delete=models.PROTECT, related_name="+", verbose_name="آزمایش")
    value = models.DecimalField("مقدار", max_digits=14, decimal_places=4)
    unit = models.CharField("واحد", max_length=30, blank=True)

    class Meta:
        verbose_name = "مقدار آزمایش بیرون"
        verbose_name_plural = "مقادیر آزمایش بیرون"

    def __str__(self):
        return f"{self.test.code}: {self.value.normalize()}"

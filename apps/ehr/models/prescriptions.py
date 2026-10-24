"""نسخه داخلی درمانگاه: پزشک از فهرست ژنریک (یا کالای مشخص داروخانه) نسخه می‌نویسد و داروساز از همان نسخه تحویل می‌دهد.

تحویل با فاکتور فروش ERP انجام می‌شود (ردیف فاکتور به ردیف نسخه وصل است)، پس موجودی و حسابداری فقط از مسیر اسناد تغییر می‌کند.
"""

import datetime

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum
from django.utils import timezone

from apps.audit.documents import DocStatus, next_number
from apps.clinic.models import Doctor

from .catalog import GenericDrug
from .patients import Patient

PRESCRIPTION_VALID_DAYS = 30


class Prescription(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "پیش‌نویس"
        ISSUED = "issued", "صادرشده"
        CANCELLED = "cancelled", "لغوشده"

    number = models.CharField("شماره نسخه", max_length=24, unique=True, null=True, blank=True, editable=False)
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="prescriptions", verbose_name="بیمار")
    encounter = models.ForeignKey(
        "ehr.Encounter", on_delete=models.PROTECT, null=True, blank=True, related_name="prescriptions", verbose_name="ویزیت"
    )
    prescriber = models.ForeignKey(Doctor, on_delete=models.PROTECT, related_name="prescriptions", verbose_name="پزشک")
    status = models.CharField("وضعیت", max_length=10, choices=Status.choices, default=Status.DRAFT, editable=False)
    note = models.CharField("توضیح برای داروساز", max_length=300, blank=True)
    insurance_tracking_code = models.CharField(
        "کد رهگیری نسخه الکترونیک بیمه", max_length=40, blank=True, help_text="اگر نسخه در سامانه بیمه هم ثبت شده"
    )
    issued_at = models.DateTimeField("زمان صدور", null=True, blank=True, editable=False)
    valid_until = models.DateField("اعتبار تا", null=True, blank=True, editable=False)
    cancelled_reason = models.CharField("علت لغو", max_length=300, blank=True, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name="ایجادکننده")
    created_at = models.DateTimeField("زمان ایجاد", auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "نسخه"
        verbose_name_plural = "نسخه‌ها"
        permissions = [("dispense_prescription", "تحویل نسخه درمانگاه در داروخانه")]

    def __str__(self):
        return self.number or f"پیش‌نویس نسخه {self.pk}"

    @property
    def is_draft(self):
        return self.status == self.Status.DRAFT

    @property
    def is_valid(self):
        return self.status == self.Status.ISSUED and (self.valid_until is None or self.valid_until >= timezone.localdate())

    @property
    def dispense_status(self):
        if self.status != self.Status.ISSUED:
            return self.get_status_display()
        items = list(self.items.all())
        dispensed = [item.dispensed_quantity for item in items]
        if items and all(d >= item.quantity for d, item in zip(dispensed, items)):
            return "تحویل کامل"
        if any(dispensed):
            return "تحویل ناقص"
        return "تحویل نشده" if self.is_valid else "منقضی"

    def issue(self, user):
        if not self.is_draft:
            raise ValidationError("این نسخه قبلاً صادر شده است.")
        if not self.items.exists():
            raise ValidationError("نسخه دارو ندارد.")
        self.number = next_number("RX")
        self.status = self.Status.ISSUED
        self.issued_at = timezone.now()
        self.valid_until = timezone.localdate() + datetime.timedelta(days=PRESCRIPTION_VALID_DAYS)
        super().save()

    def cancel(self, reason):
        if self.status != self.Status.ISSUED or any(item.dispensed_quantity for item in self.items.all()):
            raise ValidationError("فقط نسخه صادرشده و تحویل‌نشده لغو می‌شود.")
        self.status = self.Status.CANCELLED
        self.cancelled_reason = reason
        super().save()

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            old = Prescription.objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if old != self.Status.DRAFT:
                raise ValidationError("نسخه صادرشده قابل ویرایش نیست.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if not self.is_draft:
            raise ValidationError("نسخه صادرشده حذف نمی‌شود؛ آن را لغو کنید.")
        return super().delete(*args, **kwargs)


class PrescriptionItem(models.Model):
    prescription = models.ForeignKey(Prescription, on_delete=models.CASCADE, related_name="items", verbose_name="نسخه")
    generic = models.ForeignKey(GenericDrug, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name="داروی ژنریک")
    product = models.ForeignKey(
        "shop.Product", on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name="کالای مشخص داروخانه",
        help_text="برای برند خاص یا تجهیزات پزشکی (مثل گلوکومتر)",
    )
    quantity = models.PositiveIntegerField("تعداد")
    dose = models.CharField("مقدار هر نوبت", max_length=60, blank=True, help_text="مثلاً ۱ عدد، ۵ میلی‌لیتر")
    frequency = models.CharField("دفعات مصرف", max_length=80, blank=True, help_text="مثلاً هر ۸ ساعت")
    duration_days = models.PositiveSmallIntegerField("مدت مصرف (روز)", null=True, blank=True)
    instructions = models.CharField("دستور مصرف", max_length=300, blank=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "ردیف نسخه"
        verbose_name_plural = "ردیف‌های نسخه"
        constraints = [
            models.CheckConstraint(condition=models.Q(generic__isnull=False) | models.Q(product__isnull=False), name="rx_item_has_drug"),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="rx_item_quantity_positive"),
        ]

    def __str__(self):
        return f"{self.label} × {self.quantity}"

    @property
    def label(self):
        return self.product.name if self.product_id else str(self.generic)

    @property
    def sig(self):
        return " · ".join(part for part in (self.dose, self.frequency, self.duration_days and f"{self.duration_days} روز",
                                            self.instructions) if part)

    @property
    def dispensed_quantity(self):
        from apps.sales.models import SalesInvoiceLine, SalesReturnLine

        sold = SalesInvoiceLine.objects.filter(prescription_item=self, document__status=DocStatus.POSTED).aggregate(
            s=Sum("quantity"))["s"] or 0
        returned = SalesReturnLine.objects.filter(
            invoice_line__prescription_item=self, document__status=DocStatus.POSTED
        ).aggregate(s=Sum("quantity"))["s"] or 0
        return sold - returned

    @property
    def remaining(self):
        return max(self.quantity - self.dispensed_quantity, 0)

    def candidate_products(self):
        """کالاهای داروخانه که می‌توانند این ردیف را تحویل دهند (برای نمایش موجودی به پزشک و داروساز)."""
        from apps.shop.models import Product

        if self.product_id:
            return Product.objects.filter(pk=self.product_id)
        return Product.objects.filter(generic=self.generic, is_active=True)

    def save(self, *args, **kwargs):
        if self.prescription_id and not Prescription.objects.filter(pk=self.prescription_id, status=Prescription.Status.DRAFT).exists():
            raise ValidationError("نسخه صادرشده قابل ویرایش نیست.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if not Prescription.objects.filter(pk=self.prescription_id, status=Prescription.Status.DRAFT).exists():
            raise ValidationError("نسخه صادرشده قابل ویرایش نیست.")
        return super().delete(*args, **kwargs)


class DrugCounseling(models.Model):
    """مشاوره دارویی داروساز یا پزشک به بیمار (نحوه مصرف، عوارض، تداخل، نگهداری)."""

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="counselings", verbose_name="بیمار")
    prescription = models.ForeignKey(Prescription, on_delete=models.PROTECT, null=True, blank=True, related_name="counselings", verbose_name="نسخه")
    topics = models.CharField("موضوع", max_length=200, help_text="مثلاً نحوه تزریق انسولین، عوارض متفورمین")
    notes = models.TextField("شرح مشاوره", blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name="مشاور")
    created_at = models.DateTimeField("زمان", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "مشاوره دارویی"
        verbose_name_plural = "مشاوره‌های دارویی"

    def __str__(self):
        return f"{self.patient.full_name}: {self.topics}"

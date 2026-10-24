"""پایه مشترک اسناد: پیش‌نویس ← ثبت نهایی ← برگشت.

قواعد:
- سند پیش‌نویس هیچ اثری بر موجودی یا حساب‌ها ندارد و قابل ویرایش و حذف است.
- ثبت نهایی و برگشت درون یک تراکنش پایگاه داده و با قفل ردیف سند انجام می‌شود تا دو بار ثبت نشود.
- سند ثبت‌شده و ردیف‌های آن قابل ویرایش یا حذف نیستند؛ اصلاح فقط با برگشت (سند معکوس) یا سند اصلاحی ممکن است.
"""

import jdatetime
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from . import log
from .models import AuditLog, DocumentSequence


class DocumentError(ValidationError):
    def __str__(self):
        return " ".join(self.messages)


class DocumentLocked(DocumentError):
    def __init__(self, message="سند ثبت نهایی شده و قابل ویرایش یا حذف نیست. برای اصلاح، سند را برگشت بزنید."):
        super().__init__(message)


def next_number(prefix, date=None):
    """شماره یکتای سند مثل GR-1405-00012 (قفل ردیف شمارنده، ایمن در برابر ثبت هم‌زمان)."""
    date = date or timezone.localdate()
    year = jdatetime.date.fromgregorian(date=date).year
    key = f"{prefix}-{year}"
    with transaction.atomic():
        DocumentSequence.objects.get_or_create(key=key)
        seq = DocumentSequence.objects.select_for_update().get(key=key)
        seq.last_value += 1
        seq.save(update_fields=["last_value"])
    return f"{key}-{seq.last_value:05d}"


class DocStatus(models.TextChoices):
    DRAFT = "draft", "پیش‌نویس"
    POSTED = "posted", "ثبت نهایی"
    REVERSED = "reversed", "برگشت‌خورده"


class PostedDocument(models.Model):
    NUMBER_PREFIX = "DOC"

    Status = DocStatus

    number = models.CharField("شماره سند", max_length=24, unique=True, null=True, blank=True, editable=False)
    status = models.CharField("وضعیت", max_length=10, choices=DocStatus.choices, default=DocStatus.DRAFT, editable=False)
    date = models.DateField("تاریخ سند", default=timezone.localdate)
    note = models.CharField("توضیحات", max_length=500, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+",
        verbose_name="ایجادکننده", editable=False,
    )
    created_at = models.DateTimeField("زمان ایجاد", auto_now_add=True)
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+",
        verbose_name="ثبت‌کننده", editable=False,
    )
    posted_at = models.DateTimeField("زمان ثبت نهایی", null=True, blank=True, editable=False)
    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+",
        verbose_name="برگشت‌زننده", editable=False,
    )
    reversed_at = models.DateTimeField("زمان برگشت", null=True, blank=True, editable=False)
    reverse_reason = models.CharField("علت برگشت", max_length=300, blank=True, editable=False)

    class Meta:
        abstract = True

    def __str__(self):
        return self.number or f"{self._meta.verbose_name} (پیش‌نویس #{self.pk})"

    @property
    def is_draft(self):
        return self.status == DocStatus.DRAFT

    # --- قفل اسناد نهایی ---
    def save(self, *args, **kwargs):
        if self.pk and not getattr(self, "_status_change", False):
            stored = type(self)._default_manager.filter(pk=self.pk).values_list("status", flat=True).first()
            if stored and stored != DocStatus.DRAFT:
                raise DocumentLocked()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != DocStatus.DRAFT:
            raise DocumentLocked()
        return super().delete(*args, **kwargs)

    def _save_status(self, fields):
        self._status_change = True
        try:
            super().save(update_fields=fields)
        finally:
            self._status_change = False

    # --- چرخه ثبت ---
    def validate_for_post(self):
        """خطاها را با DocumentError اعلام کنید."""

    def perform_post(self, user):
        raise NotImplementedError

    def validate_for_reverse(self):
        pass

    def perform_reverse(self, user):
        raise NotImplementedError

    def _locked(self):
        return type(self)._default_manager.select_for_update().get(pk=self.pk)

    def post(self, user):
        with transaction.atomic():
            doc = self._locked()
            if doc.status != DocStatus.DRAFT:
                raise DocumentError("این سند قبلاً ثبت نهایی شده است.")
            doc.validate_for_post()
            doc.number = next_number(self.NUMBER_PREFIX, doc.date)
            doc.status = DocStatus.POSTED
            doc.posted_by = user
            doc.posted_at = timezone.now()
            doc._save_status(["number", "status", "posted_by", "posted_at"])
            doc.perform_post(user)
            log.record(AuditLog.Action.POST, doc, message=f"ثبت نهایی {doc.number}", user=user)
        self.refresh_from_db()
        return self

    def reverse(self, user, reason):
        if not (reason or "").strip():
            raise DocumentError("علت برگشت را وارد کنید.")
        with transaction.atomic():
            doc = self._locked()
            if doc.status != DocStatus.POSTED:
                raise DocumentError("فقط سند ثبت‌شده قابل برگشت است.")
            doc.validate_for_reverse()
            doc.status = DocStatus.REVERSED
            doc.reversed_by = user
            doc.reversed_at = timezone.now()
            doc.reverse_reason = reason.strip()[:300]
            doc._save_status(["status", "reversed_by", "reversed_at", "reverse_reason"])
            doc.perform_reverse(user)
            log.record(AuditLog.Action.REVERSE, doc, message=f"برگشت {doc.number}: {reason}", user=user)
        self.refresh_from_db()
        return self


class DocumentLine(models.Model):
    """ردیف سند؛ پس از ثبت نهایی سند قابل تغییر نیست."""

    DOCUMENT_FIELD = "document"

    class Meta:
        abstract = True

    def _parent_locked(self):
        parent_id = getattr(self, f"{self.DOCUMENT_FIELD}_id")
        if not parent_id:
            return False
        parent_model = self._meta.get_field(self.DOCUMENT_FIELD).related_model
        status = parent_model._default_manager.filter(pk=parent_id).values_list("status", flat=True).first()
        return status is not None and status != DocStatus.DRAFT

    def save(self, *args, **kwargs):
        if self._parent_locked() and not getattr(self, "_system_write", False):
            raise DocumentLocked()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self._parent_locked():
            raise DocumentLocked()
        return super().delete(*args, **kwargs)


class ImmutableQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise PermissionError("ردیف‌های دفتر قابل ویرایش نیستند.")

    def delete(self):
        raise PermissionError("ردیف‌های دفتر قابل حذف نیستند.")


class ImmutableRecord(models.Model):
    """ردیف دفتر (حرکت کالا، آرتیکل حسابداری): فقط ایجاد می‌شود."""

    objects = ImmutableQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            raise PermissionError("ردیف‌های دفتر قابل ویرایش نیستند.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("ردیف‌های دفتر قابل حذف نیستند.")

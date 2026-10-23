from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    """گزارش تغییرات و رویدادها. فقط افزودنی است؛ ویرایش و حذف آن ممکن نیست."""

    class Action(models.TextChoices):
        CREATE = "create", "ایجاد"
        UPDATE = "update", "ویرایش"
        DELETE = "delete", "حذف"
        POST = "post", "ثبت نهایی سند"
        REVERSE = "reverse", "برگشت سند"
        STATUS = "status", "تغییر وضعیت"
        LOGIN = "login", "ورود"
        LOGIN_FAILED = "login_failed", "ورود ناموفق"
        BACKUP = "backup", "پشتیبان‌گیری"
        EVENT = "event", "رویداد"

    created_at = models.DateTimeField("زمان", auto_now_add=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name="کاربر"
    )
    ip = models.CharField("IP", max_length=45, blank=True)
    action = models.CharField("عملیات", max_length=12, choices=Action.choices)
    model = models.CharField("مدل", max_length=100, blank=True, db_index=True)
    object_id = models.CharField("شناسه", max_length=64, blank=True, db_index=True)
    object_repr = models.CharField("عنوان", max_length=200, blank=True)
    changes = models.JSONField("تغییرات", default=dict, blank=True)
    message = models.CharField("توضیح", max_length=500, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "گزارش تغییرات"
        verbose_name_plural = "گزارش تغییرات و رویدادها"

    def __str__(self):
        return f"{self.get_action_display()} {self.model} {self.object_repr}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("گزارش تغییرات قابل ویرایش نیست.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("گزارش تغییرات قابل حذف نیست.")


class DocumentSequence(models.Model):
    """شمارنده یکتای اسناد به ازای هر نوع سند و سال شمسی."""

    key = models.CharField("کلید", max_length=40, unique=True)
    last_value = models.PositiveIntegerField("آخرین شماره", default=0)

    class Meta:
        verbose_name = "شمارنده اسناد"
        verbose_name_plural = "شمارنده‌های اسناد"

    def __str__(self):
        return f"{self.key}: {self.last_value}"

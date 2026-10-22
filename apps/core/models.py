from django.db import models

from .validators import validate_mobile


class ContactMessage(models.Model):
    name = models.CharField("نام", max_length=120)
    mobile = models.CharField("موبایل", max_length=11, validators=[validate_mobile])
    subject = models.CharField("موضوع", max_length=200)
    message = models.TextField("پیام", max_length=3000)
    is_read = models.BooleanField("خوانده شده", default=False)
    created_at = models.DateTimeField("زمان ارسال", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "پیام تماس"
        verbose_name_plural = "پیام‌های تماس"

    def __str__(self):
        return f"{self.name} - {self.subject}"

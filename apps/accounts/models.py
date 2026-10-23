import secrets

from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.urls import reverse

from apps.core.validators import validate_mobile

REFERRAL_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_referral_code():
    return "".join(secrets.choice(REFERRAL_ALPHABET) for _ in range(6))


class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile", verbose_name="کاربر")
    mobile = models.CharField("موبایل", max_length=11, unique=True, null=True, blank=True, validators=[validate_mobile])
    referral_code = models.CharField("کد معرف", max_length=8, unique=True, default=generate_referral_code, editable=False)
    referred_by = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="referrals", verbose_name="معرفی‌شده توسط"
    )
    created_at = models.DateTimeField("زمان عضویت", auto_now_add=True)

    class Meta:
        verbose_name = "عضو باشگاه"
        verbose_name_plural = "اعضای باشگاه مشتریان"

    def __str__(self):
        return self.user.get_full_name() or self.user.get_username()

    @property
    def points(self):
        return self.user.point_transactions.aggregate(total=Sum("amount"))["total"] or 0

    def referral_url(self, request):
        return request.build_absolute_uri(reverse("accounts:signup") + f"?ref={self.referral_code}")


class PointTransaction(models.Model):
    """دفتر ژتون‌های باشگاه مشتریان. مانده هر کاربر = جمع همه ردیف‌ها."""

    class Reason(models.TextChoices):
        APPOINTMENT = "appointment", "ویزیت / خدمت درمانی"
        ORDER = "order", "خرید از داروخانه"
        REFERRAL = "referral", "معرفی دوست"
        WELCOME = "welcome", "هدیه عضویت با کد معرف"
        REDEEM = "redeem", "استفاده از ژتون"
        ADJUST = "adjust", "اصلاح توسط مدیریت"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="point_transactions", verbose_name="کاربر"
    )
    amount = models.IntegerField("تعداد ژتون", help_text="برای کسر ژتون عدد منفی وارد کنید.")
    reason = models.CharField("علت", max_length=12, choices=Reason.choices)
    description = models.CharField("توضیح", max_length=200, blank=True)
    reference = models.CharField("شناسه مرجع", max_length=64, blank=True, editable=False)
    created_at = models.DateTimeField("زمان", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "تراکنش ژتون"
        verbose_name_plural = "تراکنش‌های ژتون"
        constraints = [
            # هر خدمت فقط یک بار ژتون می‌دهد.
            models.UniqueConstraint(
                fields=["user", "reason", "reference"],
                condition=~models.Q(reference=""),
                name="unique_point_award_per_reference",
            )
        ]

    def __str__(self):
        return f"{self.user} {self.amount:+d} ({self.get_reason_display()})"


class Reward(models.Model):
    """جایزه‌ای که عضو باشگاه می‌تواند با ژتون دریافت کند."""

    title = models.CharField("عنوان جایزه", max_length=150)
    description = models.CharField("توضیح", max_length=300, blank=True)
    cost = models.PositiveIntegerField("ژتون لازم")
    is_active = models.BooleanField("فعال", default=True)
    order = models.PositiveSmallIntegerField("ترتیب", default=0)

    class Meta:
        ordering = ["order", "cost"]
        verbose_name = "جایزه"
        verbose_name_plural = "جوایز باشگاه"

    def __str__(self):
        return self.title


def generate_redemption_code():
    return "R" + "".join(secrets.choice(REFERRAL_ALPHABET) for _ in range(7))


class Redemption(models.Model):
    """دریافت جایزه با ژتون؛ کد آن در پذیرش ارائه می‌شود."""

    class Status(models.TextChoices):
        ISSUED = "issued", "صادر شده"
        USED = "used", "استفاده شده"
        CANCELLED = "cancelled", "لغو شده (ژتون برگشت داده شد)"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="redemptions", verbose_name="کاربر")
    reward = models.ForeignKey(Reward, on_delete=models.PROTECT, related_name="redemptions", verbose_name="جایزه")
    cost = models.PositiveIntegerField("ژتون کسرشده")
    code = models.CharField("کد جایزه", max_length=8, unique=True, default=generate_redemption_code, editable=False)
    status = models.CharField("وضعیت", max_length=10, choices=Status.choices, default=Status.ISSUED)
    created_at = models.DateTimeField("زمان دریافت", auto_now_add=True)
    used_at = models.DateTimeField("زمان استفاده", null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "جایزه دریافت‌شده"
        verbose_name_plural = "جوایز دریافت‌شده"

    def __str__(self):
        return f"{self.code} - {self.reward}"

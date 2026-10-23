"""قوانین باشگاه مشتریان: اعطای ژتون برای هر خدمت و معرفی دوستان."""

from django.conf import settings
from django.db import IntegrityError, transaction

from .models import PointTransaction, Profile

Reason = PointTransaction.Reason


def rule(name):
    return settings.CLUB_RULES[name]


def award(user, reason, amount, reference, description=""):
    """اعطای ژتون؛ اگر قبلاً برای همین مرجع داده شده باشد کاری نمی‌کند. خروجی: ردیف جدید یا None."""
    if not user or amount <= 0:
        return None
    try:
        with transaction.atomic():
            return PointTransaction.objects.create(
                user=user, reason=reason, amount=amount, reference=reference, description=description
            )
    except IntegrityError:
        return None


def service_completed(user, reason, amount, reference, description=""):
    """پس از هر خدمت تکمیل‌شده: ژتون خدمت + در صورت داشتن معرف، ژتون معرفی (فقط بار اول)."""
    created = award(user, reason, amount, reference, description)
    if created:
        _reward_referral(user)
    return created


def _reward_referral(user):
    profile = Profile.objects.filter(user=user).select_related("referred_by__user").first()
    if not profile or not profile.referred_by:
        return
    friend_name = user.get_full_name() or user.get_username()
    award(
        profile.referred_by.user, Reason.REFERRAL, rule("referral_referrer"), f"user:{user.pk}",
        f"اولین خدمت دوست معرفی‌شده: {friend_name}",
    )
    award(user, Reason.WELCOME, rule("referral_new_member"), "welcome", "هدیه عضویت با کد معرف")


def order_points(total_rial):
    per = rule("order_rial_per_point")
    return int(total_rial // per) if per else 0



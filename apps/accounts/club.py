"""قوانین باشگاه مشتریان: اعطای ژتون برای هر خدمت و معرفی دوستان."""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Sum

from .models import PointTransaction, Profile, Redemption

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




def lifetime_points(user):
    """مجموع ژتون‌های به‌دست‌آمده (بدون کسر جوایز)؛ مبنای سطح عضویت."""
    return (
        PointTransaction.objects.filter(user=user, amount__gt=0)
        .exclude(reason=Reason.ADJUST, reference__startswith="refund:")
        .aggregate(total=Sum("amount"))["total"]
        or 0
    )


def level_for(user):
    """سطح عضویت و فاصله تا سطح بعد."""
    earned = lifetime_points(user)
    levels = settings.CLUB_LEVELS  # [(نام، حداقل ژتون)] به ترتیب صعودی
    current = levels[0]
    following = None
    for level in levels:
        if earned >= level[1]:
            current = level
        else:
            following = level
            break
    return {
        "name": current[0],
        "earned": earned,
        "next_name": following[0] if following else None,
        "to_next": following[1] - earned if following else 0,
    }


class NotEnoughPoints(Exception):
    pass


def redeem(user, reward):
    """کسر ژتون و صدور کد جایزه. قفل ردیف کاربر جلوی دوبار خرج کردن هم‌زمان را می‌گیرد."""
    with transaction.atomic():
        get_user_model().objects.select_for_update().filter(pk=user.pk).first()
        if not reward.is_active or Profile.objects.get(user=user).points < reward.cost:
            raise NotEnoughPoints
        redemption = Redemption.objects.create(user=user, reward=reward, cost=reward.cost)
        PointTransaction.objects.create(
            user=user, amount=-reward.cost, reason=Reason.REDEEM, reference=f"redemption:{redemption.pk}",
            description=f"دریافت جایزه: {reward.title}",
        )
    return redemption


def cancel_redemption(redemption):
    """لغو جایزه استفاده‌نشده و برگرداندن ژتون (فقط یک بار)."""
    if redemption.status != Redemption.Status.ISSUED:
        return False
    with transaction.atomic():
        redemption.status = Redemption.Status.CANCELLED
        redemption.save(update_fields=["status"])
        award(redemption.user, Reason.ADJUST, redemption.cost, f"refund:{redemption.pk}", f"برگشت ژتون جایزه {redemption.code}")
    return True

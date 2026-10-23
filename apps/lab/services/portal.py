"""دسترسی بیمار به جواب: کد یک‌بارمصرف پیامکی، محدودیت تلاش و نشست کوتاه‌مدت."""

import datetime
import secrets

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from apps.audit.documents import DocStatus, DocumentError

from ..models import AccessCode, LabOrder, LabPatient
from ..models.portal import hash_code
from . import notify

SESSION_KEY = "lab_result_access"


def rate_limited(key, limit=None, window=3600):
    """True اگر از سقف تلاش در بازه گذشته باشد. شمارنده در cache (برای چند پردازه، cache پایگاه داده یا Redis)."""
    limit = limit or settings.LAB_RULES["lookup_limit_per_hour"]
    cache_key = f"lab-rl:{key}"
    count = cache.get(cache_key, 0)
    if count >= limit:
        return True
    cache.set(cache_key, count + 1, window)
    return False


def find_order(number, identifier):
    order = LabOrder.objects.select_related("patient").filter(number__iexact=number.strip(), status=DocStatus.POSTED).first()
    if order is None:
        return None
    identifier = identifier.strip()
    if identifier not in (order.patient.national_code or "", order.patient.foreign_id or ""):
        return None
    return order


def issue_code(patient, purpose, order=None, ip=""):
    last = AccessCode.objects.filter(patient=patient, purpose=purpose, used_at__isnull=True).first()
    resend = settings.LAB_RULES["otp_resend_seconds"]
    if last and (timezone.now() - last.created_at).total_seconds() < resend:
        return last  # کد قبلی هنوز تازه است؛ پیامک دوباره ارسال نمی‌شود.
    code = f"{secrets.randbelow(10**6):06d}"
    access = AccessCode.objects.create(
        purpose=purpose, patient=patient, order=order, code_hash=hash_code(code), ip=ip[:45],
        expires_at=timezone.now() + datetime.timedelta(seconds=settings.LAB_RULES["otp_ttl_seconds"]),
    )
    notify.otp(patient.mobile, code)
    return access


def check_code(access, code):
    """کد را بررسی می‌کند؛ پس از سقف تلاش یا انقضا باطل می‌شود."""
    if access is None or access.used_at or access.expires_at < timezone.now():
        raise DocumentError("کد منقضی شده است؛ دوباره درخواست کنید.")
    if access.attempts >= settings.LAB_RULES["otp_max_attempts"]:
        raise DocumentError("تعداد تلاش‌ها بیش از حد مجاز است؛ دوباره درخواست کنید.")
    if not access.matches(code.strip()):
        access.attempts += 1
        access.save(update_fields=["attempts"])
        raise DocumentError("کد وارد شده درست نیست.")
    access.used_at = timezone.now()
    access.save(update_fields=["used_at"])
    return access


def grant(request, order):
    access = request.session.get(SESSION_KEY, {})
    expiry = timezone.now() + datetime.timedelta(minutes=settings.LAB_RULES["result_access_minutes"])
    access[str(order.pk)] = expiry.timestamp()
    request.session[SESSION_KEY] = access


def can_view(request, order):
    user = request.user
    if user.is_authenticated and order.patient.user_id == user.pk:
        return True
    expiry = request.session.get(SESSION_KEY, {}).get(str(order.pk))
    return bool(expiry and expiry > timezone.now().timestamp())


def link_patient(user, patient):
    if patient.user_id and patient.user_id != user.pk:
        raise DocumentError("این پرونده به حساب دیگری متصل است؛ با آزمایشگاه تماس بگیرید.")
    if LabPatient.objects.filter(user=user).exclude(pk=patient.pk).exists():
        raise DocumentError("حساب شما قبلاً به پرونده دیگری متصل شده است.")
    patient.user = user
    patient.save(update_fields=["user"])

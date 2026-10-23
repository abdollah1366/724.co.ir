from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in, user_login_failed
from django.dispatch import receiver

from . import log
from .context import acting_as, client_ip
from .models import AuditLog

log.track(get_user_model())


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    with acting_as(user, client_ip(request) if request else ""):
        log.record(AuditLog.Action.LOGIN, user, message="ورود موفق")


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request=None, **kwargs):
    with acting_as(None, client_ip(request) if request else ""):
        # فقط نام کاربری ثبت می‌شود، هرگز رمز.
        log.record(AuditLog.Action.LOGIN_FAILED, message=f"ورود ناموفق: {credentials.get('username', '')}"[:200])

"""محدودیت تلاش ورود: جلوی حدس رمز (brute force) روی ورود سایت و پنل مدیریت را می‌گیرد.

پس از LOGIN_FAILURE_LIMIT تلاش ناموفق برای یک نام کاربری، یا LOGIN_IP_FAILURE_LIMIT تلاش ناموفق از یک IP، ورود
تا LOGIN_LOCK_MINUTES دقیقه بسته می‌شود (حتی با رمز درست). ورود موفق شمارنده همان نام کاربری را صفر می‌کند.
شمارنده در cache است؛ روی سرور با DJANGO_CACHE_BACKEND=db بین پردازه‌ها مشترک است.
"""

from django.conf import settings
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.signals import user_logged_in, user_login_failed
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.dispatch import receiver

from apps.core.validators import normalize_digits

LOCK_MESSAGE = "به دلیل تلاش‌های ناموفق زیاد، ورود موقتاً بسته شد. چند دقیقه دیگر دوباره تلاش کنید."


def _limits():
    return (getattr(settings, "LOGIN_FAILURE_LIMIT", 8), getattr(settings, "LOGIN_IP_FAILURE_LIMIT", 30),
            getattr(settings, "LOGIN_LOCK_MINUTES", 15) * 60)


def _ip(request):
    return (request.META.get("REMOTE_ADDR") or "-") if request is not None else "-"


def _keys(request, username):
    name = normalize_digits(username or "").strip().lower()[:150]
    return f"login-fail:u:{name}", f"login-fail:ip:{_ip(request)}"


def is_locked(request, username):
    user_limit, ip_limit, _ = _limits()
    user_key, ip_key = _keys(request, username)
    return cache.get(user_key, 0) >= user_limit or cache.get(ip_key, 0) >= ip_limit


def record_failure(request, username):
    _, _, window = _limits()
    for key in _keys(request, username):
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, window)


class ThrottleBackend(ModelBackend):
    """اولین backend: اگر نام کاربری یا IP قفل باشد، کل ورود را متوقف می‌کند (PermissionDenied)."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is not None and is_locked(request, username):
            raise PermissionDenied(LOCK_MESSAGE)
        return None


@receiver(user_login_failed)
def _on_failure(sender, credentials, request=None, **kwargs):
    record_failure(request, credentials.get("username"))


@receiver(user_logged_in)
def _on_success(sender, request, user, **kwargs):
    cache.delete(_keys(request, user.get_username())[0])
    data = request.POST if request is not None and request.method == "POST" else {}
    if data.get("username"):
        cache.delete(_keys(request, data["username"])[0])


class LockMessageMixin:
    """فرم ورود: اگر قفل است، پیام روشن به جای «نام کاربری یا رمز اشتباه است»."""

    def clean(self):
        username = self.cleaned_data.get("username")
        if username and is_locked(self.request, username):
            from django import forms

            raise forms.ValidationError(LOCK_MESSAGE, code="locked")
        return super().clean()

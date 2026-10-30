"""ورود کارکنان با کد ملی، حتی اگر نام کاربری حسابشان چیز دیگری باشد (حساب‌های قدیمی)."""

from django.contrib.auth.backends import ModelBackend

from apps.core.validators import normalize_digits

from .models import StaffAccount


class NationalCodeBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        code = normalize_digits(username or "").strip()
        if len(code) != 10 or not code.isdigit():
            return None
        account = StaffAccount.objects.select_related("user").filter(national_code=code).first()
        if account is None or account.user.get_username() == code:
            return None  # نام کاربری خودش کد ملی است؛ ModelBackend همان را بررسی می‌کند
        user = account.user
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None

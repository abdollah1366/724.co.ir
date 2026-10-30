"""کارمندی که با رمز پیش‌فرض (شماره موبایل) وارد شده تا رمز را عوض نکند به هیچ صفحه دیگری نمی‌رود."""

from django.shortcuts import redirect
from django.urls import reverse

from .models import StaffAccount


class ForcePasswordChangeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and user.is_staff and self._must_change(request, user):
            allowed = (reverse("accounts:password_change"), reverse("accounts:logout"), reverse("admin:logout"))
            if request.path not in allowed and not request.path.startswith(("/static/", "/media/")):
                return redirect("accounts:password_change")
        return self.get_response(request)

    @staticmethod
    def _must_change(request, user):
        return StaffAccount.objects.filter(user=user, must_change_password=True).exists()

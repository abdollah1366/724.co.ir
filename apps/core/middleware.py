"""IP واقعی کاربر پشت Nginx."""

import contextvars
import ipaddress
import re
import uuid

from django.conf import settings


class RealIPMiddleware:
    """با DJANGO_BEHIND_PROXY، مقدار REMOTE_ADDR را از X-Real-IP که Nginx می‌گذارد پر می‌کند.

    بدون آن، پشت پروکسی همه کاربران یک IP دارند: محدودیت تلاش برای همه با هم پر می‌شود
    و گزارش تغییرات IP واقعی را ثبت نمی‌کند.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if settings.BEHIND_PROXY:
            real_ip = request.META.get("HTTP_X_REAL_IP", "").strip()
            try:
                request.META["REMOTE_ADDR"] = str(ipaddress.ip_address(real_ip))
            except ValueError:
                pass
        return self.get_response(request)


_request_id = contextvars.ContextVar("request_id", default="")
_REQUEST_ID_OK = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def current_request_id():
    """شناسه همبستگی درخواست جاری (برای لاگ و رویدادها)؛ بیرون از درخواست وب خالی است."""
    return _request_id.get()


class RequestIDMiddleware:
    """هر درخواست یک شناسه همبستگی می‌گیرد (یا X-Request-ID معتبر پروکسی را نگه می‌دارد) و در پاسخ برمی‌گرداند."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.META.get("HTTP_X_REQUEST_ID", "")
        request_id = incoming if _REQUEST_ID_OK.match(incoming) else uuid.uuid4().hex
        request.request_id = request_id
        token = _request_id.set(request_id)
        try:
            response = self.get_response(request)
        finally:
            _request_id.reset(token)
        response["X-Request-ID"] = request_id
        return response


class SiteStatsMiddleware:
    """بازدید صفحه‌های عمومی را می‌شمارد (apps/core/stats.py) و برای کارکنان، باز کردن هر منوی پنل را
    (apps/core/panels.py) تا «دسترسی سریع» پرکاربردترین منوهای هر نفر را نشان دهد."""

    def __init__(self, get_response):
        self.get_response = get_response
        self._menu_names = None

    def __call__(self, request):
        response = self.get_response(request)
        from . import stats

        if stats.is_countable(request, response):
            stats.record_visit(request, response)
        else:
            self._record_menu_use(request, response)
        return response

    def _record_menu_use(self, request, response):
        user = getattr(request, "user", None)
        match = getattr(request, "resolver_match", None)
        if (request.method != "GET" or response.status_code != 200 or match is None
                or user is None or not user.is_authenticated or not user.is_staff):
            return
        if self._menu_names is None:
            from .panels import menu_names

            self._menu_names = menu_names()
        if match.view_name not in self._menu_names:
            return
        from django.db import DatabaseError, transaction
        from django.db.models import F
        from django.utils import timezone

        from .models import MenuUsage

        try:
            with transaction.atomic():
                now = timezone.now()
                if not MenuUsage.objects.filter(user=user, url_name=match.view_name).update(
                        count=F("count") + 1, last_used=now):
                    MenuUsage.objects.create(user=user, url_name=match.view_name, count=1, last_used=now)
        except DatabaseError:
            pass

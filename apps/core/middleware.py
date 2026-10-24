"""IP واقعی کاربر پشت Nginx."""

import ipaddress

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

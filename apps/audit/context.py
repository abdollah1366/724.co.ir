"""نگهداری کاربر و IP درخواست جاری برای ثبت در گزارش تغییرات."""

import contextlib
import contextvars

_current = contextvars.ContextVar("audit_actor", default=(None, ""))


def get_actor():
    """خروجی: (کاربر، IP). بیرون از درخواست وب (دستورها و آزمون‌ها) کاربر None است مگر با acting_as تعیین شود."""
    return _current.get()


@contextlib.contextmanager
def acting_as(user, ip=""):
    token = _current.set((user if user and user.is_authenticated else None, ip))
    try:
        yield
    finally:
        _current.reset(token)


def client_ip(request):
    return (request.META.get("REMOTE_ADDR") or "")[:45]


class AuditContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        with acting_as(user, client_ip(request)):
            return self.get_response(request)

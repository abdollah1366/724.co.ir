"""نوبت نمونه‌گیری حضوری و در منزل با ظرفیت هر بازه زمانی."""

import datetime

from django.conf import settings
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from apps.audit.documents import DocumentError

from ..models import SamplingAppointment, SamplingSchedule


def _schedules(kind, date):
    return SamplingSchedule.objects.filter(kind=kind, weekday=date.weekday(), is_active=True)


def slot_capacity(kind, date):
    """{زمان: ظرفیت} برای یک روز."""
    capacity = {}
    for schedule in _schedules(kind, date):
        for time in schedule.slot_times():
            capacity[time] = capacity.get(time, 0) + schedule.capacity
    return capacity


def available_slots(kind, date):
    """[(زمان، جای خالی)] برای روزهای مجاز آینده؛ زمان‌های گذشته امروز حذف می‌شوند."""
    today = timezone.localdate()
    if date < today or date > today + datetime.timedelta(days=settings.LAB_RULES["booking_days_ahead"]):
        return []
    capacity = slot_capacity(kind, date)
    booked = dict(
        SamplingAppointment.objects.active().filter(kind=kind, date=date).values("time").annotate(n=Count("id")).values_list("time", "n")
    )
    now = timezone.localtime().time() if date == today else None
    return [
        (time, cap - booked.get(time, 0))
        for time, cap in sorted(capacity.items())
        if cap - booked.get(time, 0) > 0 and (now is None or time > now)
    ]


def available_dates(kind):
    today = timezone.localdate()
    days = settings.LAB_RULES["booking_days_ahead"]
    weekdays = set(SamplingSchedule.objects.filter(kind=kind, is_active=True).values_list("weekday", flat=True))
    return [today + datetime.timedelta(days=i) for i in range(days + 1) if (today + datetime.timedelta(days=i)).weekday() in weekdays]


@transaction.atomic
def book(kind, date, time, **fields):
    """ثبت نوبت با کنترل ظرفیت. ردیف‌های برنامه آن روز قفل می‌شوند تا دو درخواست هم‌زمان از ظرفیت عبور نکنند."""
    list(_schedules(kind, date).select_for_update())
    remaining = dict(available_slots(kind, date))
    if time not in remaining:
        raise DocumentError("این زمان پر شده یا قابل رزرو نیست؛ زمان دیگری انتخاب کنید.")
    tests = fields.pop("tests", [])
    duplicate = SamplingAppointment.objects.active().filter(date=date, national_code=fields["national_code"]).exists()
    if duplicate:
        raise DocumentError("برای این کد ملی در همین روز نوبت فعال وجود دارد.")
    appointment = SamplingAppointment.objects.create(kind=kind, date=date, time=time, consent_at=timezone.now(), **fields)
    if tests:
        appointment.tests.set(tests)
    return appointment


def cancel(appointment):
    if not appointment.is_cancellable:
        raise DocumentError("این نوبت قابل لغو نیست.")
    appointment.status = SamplingAppointment.Status.CANCELLED
    appointment.save(update_fields=["status"])

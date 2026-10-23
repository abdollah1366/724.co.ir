"""نوبت اینترنتی نمونه‌گیری (حضوری و در منزل)، پیگیری و لغو."""

import datetime

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from ..forms import BookingTrackForm, SamplingBookingForm, date_choices
from ..models import SamplingAppointment, SamplingSchedule
from ..services import booking, notify

SESSION_KEY = "lab_appointment_ids"


def _remember(request, appointment):
    ids = set(request.session.get(SESSION_KEY, []))
    ids.add(appointment.pk)
    request.session[SESSION_KEY] = list(ids)


def _session_appointment(request, pk):
    if pk not in request.session.get(SESSION_KEY, []):
        return None
    return SamplingAppointment.objects.filter(pk=pk).first()


def _parse_date(value):
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def book(request):
    kind = request.GET.get("kind") or request.POST.get("kind") or SamplingSchedule.Kind.LAB
    if kind not in SamplingSchedule.Kind.values:
        kind = SamplingSchedule.Kind.LAB
    dates = booking.available_dates(kind)
    date = _parse_date(request.GET.get("date") or request.POST.get("date"))
    if date not in dates:
        date = None
    form = None
    if date:
        form = SamplingBookingForm(request.POST if request.method == "POST" else None, kind=kind, date=date)
        if request.method == "POST" and form.is_valid():
            data = form.cleaned_data
            try:
                appointment = booking.book(
                    kind, date, data["time"], full_name=data["full_name"], national_code=data["national_code"],
                    mobile=data["mobile"], address=data.get("address", ""), note=data["note"], tests=data["tests"],
                    user=request.user if request.user.is_authenticated else None,
                )
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
                return redirect(f"{request.path}?kind={kind}&date={date.isoformat()}")
            notify.appointment_booked(appointment)
            _remember(request, appointment)
            return redirect("lab:booking_detail", pk=appointment.pk)
    return render(request, "lab/book.html", {
        "kind": kind, "kinds": SamplingSchedule.Kind.choices, "dates": date_choices(dates), "selected_date": date,
        "form": form,
    })


def booking_detail(request, pk):
    appointment = _session_appointment(request, pk)
    if appointment is None:
        return redirect("lab:booking_track")
    return render(request, "lab/booking_detail.html", {"appointment": appointment})


def booking_track(request):
    form = BookingTrackForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        appointment = form.cleaned_data["appointment"]
        _remember(request, appointment)
        return redirect("lab:booking_detail", pk=appointment.pk)
    return render(request, "lab/booking_track.html", {"form": form})


@require_POST
def booking_cancel(request, pk):
    appointment = _session_appointment(request, pk)
    if appointment is None:
        return redirect("lab:booking_track")
    try:
        booking.cancel(appointment)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, "نوبت شما لغو شد.")
    return redirect("lab:booking_detail", pk=appointment.pk)

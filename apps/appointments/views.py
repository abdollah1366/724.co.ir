from django.contrib import messages
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.clinic.models import Doctor

from .forms import BookingForm, TrackingForm
from .models import Appointment

SESSION_KEY = "appointment_ids"


def _remember(request, appointment):
    # فقط شناسه نوبت‌هایی که همین مرورگر ثبت یا پیگیری کرده در نشست نگه داشته می‌شود.
    ids = set(request.session.get(SESSION_KEY, []))
    ids.add(appointment.pk)
    request.session[SESSION_KEY] = list(ids)


def _session_appointment(request, pk):
    if pk not in request.session.get(SESSION_KEY, []):
        return None
    return Appointment.objects.select_related("doctor").filter(pk=pk).first()


def book(request, slug):
    doctor = get_object_or_404(Doctor.objects.active(), slug=slug, accepts_online_booking=True)
    if request.method == "POST":
        form = BookingForm(request.POST, doctor=doctor)
        if form.is_valid():
            try:
                with transaction.atomic():
                    appointment = form.save()
            except IntegrityError:
                messages.error(request, "این زمان همین حالا رزرو شد. لطفاً زمان دیگری انتخاب کنید.")
                return redirect(f"{request.path}?date={form.cleaned_data['date'].isoformat()}")
            _remember(request, appointment)
            return redirect("appointments:detail", pk=appointment.pk)
    else:
        form = BookingForm(doctor=doctor, selected_date=request.GET.get("date"))
    return render(request, "appointments/book.html", {"doctor": doctor, "form": form})


def detail(request, pk):
    appointment = _session_appointment(request, pk)
    if appointment is None:
        return redirect("appointments:track")
    return render(request, "appointments/detail.html", {"appointment": appointment})


def track(request):
    form = TrackingForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        appointment = form.cleaned_data["appointment"]
        _remember(request, appointment)
        return redirect("appointments:detail", pk=appointment.pk)
    return render(request, "appointments/track.html", {"form": form})


@require_POST
def cancel(request, pk):
    appointment = _session_appointment(request, pk)
    if appointment is None:
        return redirect("appointments:track")
    if appointment.can_cancel:
        appointment.status = Appointment.Status.CANCELLED
        appointment.save(update_fields=["status", "updated_at"])
        messages.success(request, "نوبت شما لغو شد.")
    else:
        messages.error(request, "این نوبت قابل لغو نیست.")
    return redirect("appointments:detail", pk=appointment.pk)

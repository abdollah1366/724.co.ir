from django.conf import settings
from django.contrib import messages
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.audit.context import client_ip
from apps.clinic.models import Doctor
from apps.lab.services.portal import rate_limited

from .forms import BookingForm, TrackingForm
from .models import Appointment

SESSION_KEY = "appointment_ids"
# نوبت‌هایی که این مرورگر ثبت کرده یا با کد پیگیری باز کرده؛ فقط این‌ها لغوشدنی‌اند (نه با موبایل یا کد ملی تنها).
CANCEL_KEY = "appointment_cancel_ids"


def _remember(request, appointment, can_cancel=True):
    # فقط شناسه نوبت‌هایی که همین مرورگر ثبت یا پیگیری کرده در نشست نگه داشته می‌شود.
    keys = (SESSION_KEY, CANCEL_KEY) if can_cancel else (SESSION_KEY,)
    for key in keys:
        ids = set(request.session.get(key, []))
        ids.add(appointment.pk)
        request.session[key] = list(ids)


def _can_cancel(request, appointment):
    return appointment.can_cancel and appointment.pk in request.session.get(CANCEL_KEY, [])


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
                    appointment = form.save(user=request.user)
            except IntegrityError:
                messages.error(request, "این زمان همین حالا رزرو شد. لطفاً زمان دیگری انتخاب کنید.")
                return redirect(f"{request.path}?date={form.cleaned_data['date'].isoformat()}")
            _remember(request, appointment)
            return redirect("appointments:detail", pk=appointment.pk)
    else:
        initial = {}
        profile = getattr(request.user, "profile", None) if request.user.is_authenticated else None
        if profile:
            initial = {"patient_full_name": request.user.get_full_name(), "mobile": profile.mobile}
        form = BookingForm(doctor=doctor, selected_date=request.GET.get("date"), initial=initial)
    return render(request, "appointments/book.html", {"doctor": doctor, "form": form})


def detail(request, pk):
    appointment = _session_appointment(request, pk)
    if appointment is None:
        return redirect("appointments:track")
    return render(request, "appointments/detail.html", {
        "appointment": appointment, "can_cancel": _can_cancel(request, appointment)})


def track(request):
    form = TrackingForm(request.POST or None)
    if request.method == "POST" and rate_limited(f"appt-track:{client_ip(request)}", limit=settings.TRACKING_LIMIT_PER_HOUR):
        messages.error(request, "تعداد تلاش‌ها زیاد است؛ یک ساعت دیگر دوباره تلاش کنید یا با پذیرش تماس بگیرید.")
        return render(request, "appointments/track.html", {"form": TrackingForm()})
    if request.method == "POST" and form.is_valid():
        appointments = form.cleaned_data["appointments"]
        for appointment in appointments:
            _remember(request, appointment, can_cancel=form.cleaned_data["by_code"])
        if len(appointments) == 1:
            return redirect("appointments:detail", pk=appointments[0].pk)
        return render(request, "appointments/track.html", {"form": TrackingForm(), "appointments": appointments})
    return render(request, "appointments/track.html", {"form": form})


@require_POST
def cancel(request, pk):
    appointment = _session_appointment(request, pk)
    if appointment is None:
        return redirect("appointments:track")
    if _can_cancel(request, appointment):
        appointment.status = Appointment.Status.CANCELLED
        appointment.save(update_fields=["status", "updated_at"])
        messages.success(request, "نوبت شما لغو شد.")
    else:
        messages.error(request, "این نوبت قابل لغو نیست.")
    return redirect("appointments:detail", pk=appointment.pk)

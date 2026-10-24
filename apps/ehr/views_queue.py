"""یادآورهای بیمار، پذیرش صف مطب، کنسول فراخوان پزشک و مانیتور سالن انتظار."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.appointments.models import Appointment
from apps.clinic.models import Doctor

from . import engagement, services
from .forms import ReminderForm, WalkInForm
from .models import ClinicTicket, Patient, Prescription, Reminder
from .views import _errors, _require_access, staff_perm


def _form_errors(request, form):
    messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))


# ---------------------------------------------------------------- یادآور


@staff_perm("ehr.add_reminder")
@require_POST
def reminder_add(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    _require_access(request, patient)
    form = ReminderForm(request.POST, prefix="rem")
    if form.is_valid():
        data = form.cleaned_data
        try:
            engagement.create_reminder(patient, data["kind"], data["due_date"], request.user, data["days_before"],
                                       note=data["note"], doctor=services.provider_for(request.user))
            messages.success(request, "یادآور ثبت شد." if patient.reminder_consent_at else
                             "یادآور ثبت شد ولی بیمار رضایت پیامکی نداده و پیامک ارسال نمی‌شود.")
        except ValidationError as exc:
            _errors(request, exc)
    else:
        _form_errors(request, form)
    return redirect(reverse("ehr:chart", args=[patient.pk]) + "#reminders")


@staff_perm("ehr.change_reminder")
@require_POST
def reminder_cancel(request, pk):
    reminder = get_object_or_404(Reminder, pk=pk)
    _require_access(request, reminder.patient)
    try:
        engagement.cancel_reminder(reminder)
    except ValidationError as exc:
        _errors(request, exc)
    return redirect(reverse("ehr:chart", args=[reminder.patient_id]) + "#reminders")


@staff_perm("ehr.add_reminder")
@require_POST
def refill_reminder(request, pk):
    prescription = get_object_or_404(Prescription, pk=pk)
    _require_access(request, prescription.patient)
    try:
        reminder = engagement.refill_reminder(prescription, request.user)
        messages.success(request, f"یادآور تمدید دارو برای {engagement._jalali(reminder.due_date)} ثبت شد.")
    except ValidationError as exc:
        _errors(request, exc)
    return redirect("ehr:prescription", pk=prescription.pk)


# ---------------------------------------------------------------- پذیرش صف


@staff_perm("ehr.add_clinicticket")
def queue_reception(request):
    """پذیرش درمانگاه: بیمار نوبت‌دار حاضر می‌شود یا بیمار بدون نوبت شماره می‌گیرد."""
    today = timezone.localdate()
    form = WalkInForm(request.POST if request.POST.get("action") == "walk_in" else None, prefix="w")
    if request.method == "POST":
        try:
            if request.POST.get("action") == "check_in":
                appointment = get_object_or_404(Appointment, pk=request.POST.get("appointment"), date=today)
                priority = ClinicTicket.Priority.SPECIAL if request.POST.get("special") else ClinicTicket.Priority.NORMAL
                ticket = engagement.check_in(appointment, request.user, priority)
                return redirect("ehr:queue_ticket", pk=ticket.pk)
            if request.POST.get("action") == "walk_in" and form.is_valid():
                data = form.cleaned_data
                priority = ClinicTicket.Priority.SPECIAL if data["special"] else ClinicTicket.Priority.NORMAL
                ticket = engagement.walk_in(data["doctor"], request.user, data["identifier"], priority)
                return redirect("ehr:queue_ticket", pk=ticket.pk)
        except ValidationError as exc:
            _errors(request, exc)
    appointments = Appointment.objects.active().filter(date=today).select_related("doctor", "ticket").order_by("doctor", "time")
    doctors = Doctor.objects.filter(tickets__date=today).distinct()
    return render(request, "ehr/queue_reception.html", {
        "appointments": appointments, "form": form, "today": today,
        "queues": [{"doctor": d, "current": engagement.current(d), "waiting": list(engagement.waiting(d))} for d in doctors],
    })


@staff_perm("ehr.add_clinicticket")
def queue_ticket(request, pk):
    ticket = get_object_or_404(ClinicTicket.objects.select_related("doctor"), pk=pk, date=timezone.localdate())
    return render(request, "ehr/queue_ticket.html", {"ticket": ticket, "ahead": engagement.ahead_of(ticket)})


# ---------------------------------------------------------------- کنسول فراخوان


def _console_doctor(request):
    """پزشک صف خودش را مدیریت می‌کند؛ پذیرش و پرستار با ?doctor= صف هر پزشک را."""
    doctor = services.provider_for(request.user)
    requested = request.POST.get("doctor") or request.GET.get("doctor")
    if requested and (doctor is None or str(doctor.pk) != requested):
        if not request.user.has_perm("ehr.add_clinicticket"):
            raise PermissionDenied
        doctor = get_object_or_404(Doctor, pk=requested)
    return doctor


@staff_perm("ehr.change_clinicticket")
@require_POST
def queue_action(request):
    doctor = _console_doctor(request)
    if doctor is None:
        raise PermissionDenied
    action = request.POST.get("action")
    ticket = None
    if request.POST.get("ticket"):
        ticket = get_object_or_404(ClinicTicket, pk=request.POST["ticket"], doctor=doctor, date=timezone.localdate())
    try:
        if action == "next":
            called = engagement.call_next(doctor)
            if called is None:
                messages.info(request, "کسی در صف نیست.")
            else:
                messages.success(request, f"شماره {called.label} فراخوانده شد.")
        elif action == "call" and ticket:
            engagement.call_ticket(ticket)
        elif action == "recall" and ticket:
            engagement.recall(ticket)
        elif action == "done" and ticket:
            engagement.finish(ticket)
        elif action == "skip" and ticket:
            engagement.finish(ticket, ClinicTicket.Status.SKIPPED)
        elif action == "cancel" and ticket:
            engagement.cancel(ticket)
    except ValidationError as exc:
        _errors(request, exc)
    target = request.POST.get("next") or reverse("ehr:my_patients")
    return redirect(target if target.startswith("/") and not target.startswith("//") else reverse("ehr:my_patients"))


# ---------------------------------------------------------------- مانیتور


def queue_display(request):
    """مانیتور تمام‌صفحه سالن انتظار درمانگاه. ?d=1,2 فقط پزشکان مشخص؛ ?lab=* فراخوان آزمایشگاه را هم نشان می‌دهد."""
    return render(request, "ehr/queue_display.html", {"query": request.GET.urlencode()})


@never_cache
def queue_display_data(request):
    doctor_ids = [int(d) for d in request.GET.get("d", "").split(",") if d.isdigit()]
    lab = [c for c in request.GET.get("lab", "").upper().split(",") if c]
    return JsonResponse(engagement.display_data(doctor_ids or None, lab or None), json_dumps_params={"ensure_ascii": False})

"""یادآور ویزیت و دارو (پیامک با رضایت) و صف حضوری مطب پزشکان."""

import datetime

import jdatetime
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Max
from django.utils import timezone

from apps.appointments.models import Appointment
from apps.clinic.models import Doctor
from apps.core.db import lock_own_rows
from apps.core.sms import send_sms
from apps.core.validators import normalize_digits

from .models import ClinicTicket, Patient, Reminder

# ---------------------------------------------------------------- یادآور


def _jalali(date):
    return jdatetime.date.fromgregorian(date=date).strftime("%Y/%m/%d")


def reminder_text(reminder):
    """متن پیامک عمداً عمومی است: نام پزشک، دارو، آزمایش یا بیماری در آن نمی‌آید."""
    return (f"{reminder.patient.first_name} عزیز، زمان مراجعه بعدی شما به درمانگاه {_jalali(reminder.due_date)} است. "
            f"برای نوبت با درمانگاه تماس بگیرید. {settings.CLINIC_NAME}")


def create_reminder(patient, kind, due_date, user, days_before=None, **extra):
    if due_date < timezone.localdate():
        raise ValidationError("موعد یادآور گذشته است.")
    days_before = settings.EHR_REMINDER_DAYS_BEFORE if days_before is None else days_before
    send_on = max(due_date - datetime.timedelta(days=days_before), timezone.localdate())
    return Reminder.objects.create(patient=patient, kind=kind, due_date=due_date, send_on=send_on, created_by=user, **extra)


def visit_reminder_from_encounter(encounter, user):
    """با امضای ویزیتی که «ویزیت بعدی» دارد، یادآور خودکار ساخته می‌شود (یک بار)."""
    if not encounter.next_visit or encounter.next_visit < timezone.localdate() or encounter.reminders.exists():
        return None
    return create_reminder(encounter.patient, Reminder.Kind.VISIT, encounter.next_visit, user, doctor=encounter.provider,
                           encounter=encounter)


def refill_due_date(prescription):
    days = [item.duration_days for item in prescription.items.all() if item.duration_days]
    if not days or not prescription.issued_at:
        return None
    return timezone.localtime(prescription.issued_at).date() + datetime.timedelta(days=max(days))


def refill_reminder(prescription, user):
    if prescription.status != prescription.Status.ISSUED:
        raise ValidationError("فقط برای نسخه صادرشده یادآور تمدید ساخته می‌شود.")
    due = refill_due_date(prescription)
    if due is None:
        raise ValidationError("مدت مصرف داروها در نسخه وارد نشده است.")
    if prescription.reminders.exclude(status=Reminder.Status.CANCELLED).exists():
        raise ValidationError("برای این نسخه قبلاً یادآور ساخته شده است.")
    return create_reminder(prescription.patient, Reminder.Kind.REFILL, due, user, days_before=settings.EHR_REFILL_DAYS_BEFORE,
                           doctor=prescription.prescriber, prescription=prescription)


def cancel_reminder(reminder):
    if reminder.status != Reminder.Status.PENDING:
        raise ValidationError("فقط یادآور ارسال‌نشده لغو می‌شود.")
    reminder.status = Reminder.Status.CANCELLED
    reminder.save(update_fields=["status"])


def due_reminders(today=None):
    today = today or timezone.localdate()
    return Reminder.objects.filter(status=Reminder.Status.PENDING, send_on__lte=today, due_date__gte=today).select_related("patient")


def send_reminders(today=None):
    """پیامک یادآورهای امروز؛ بیمار بدون رضایت یا موبایل پیامک نمی‌گیرد و وضعیت ثبت می‌شود."""
    counts = {"sent": 0, "no_consent": 0, "failed": 0}
    sent_to = set()
    for reminder in due_reminders(today):
        patient = reminder.patient
        if not (patient.reminder_consent_at and patient.mobile):
            reminder.status = Reminder.Status.NO_CONSENT
            counts["no_consent"] += 1
        elif (patient.pk, reminder.due_date) in sent_to:
            # چند یادآور همان روز برای یک بیمار: یک پیامک کافی است
            reminder.status, reminder.sent_at = Reminder.Status.SENT, timezone.now()
        else:
            sms = send_sms(patient.mobile, reminder_text(reminder), "ehr_reminder")
            ok = sms.status == sms.Status.SENT
            reminder.status = Reminder.Status.SENT if ok else Reminder.Status.FAILED
            reminder.sent_at = timezone.now() if ok else None
            counts["sent" if ok else "failed"] += 1
            sent_to.add((patient.pk, reminder.due_date))
        reminder.save(update_fields=["status", "sent_at"])
    return counts


def send_appointment_reminders(date):
    """یادآوری نوبت‌های فردای درمانگاه (نوبتی که خود بیمار گرفته؛ بدون نام پزشک یا تخصص)."""
    count = 0
    for appointment in Appointment.objects.active().filter(date=date, reminder_sent_at__isnull=True):
        first_name = appointment.patient_full_name.split()[0] if appointment.patient_full_name else ""
        send_sms(appointment.mobile,
                 f"{first_name} عزیز، نوبت شما در درمانگاه {_jalali(date)} ساعت {appointment.time:%H:%M} است. "
                 f"کد پیگیری: {appointment.tracking_code} {settings.CLINIC_NAME}", "clinic_reminder")
        Appointment.objects.filter(pk=appointment.pk).update(reminder_sent_at=timezone.now())
        count += 1
    return count


# ---------------------------------------------------------------- صف مطب

Status = ClinicTicket.Status


def _issue(doctor, user, priority=ClinicTicket.Priority.NORMAL, patient=None, appointment=None):
    today = timezone.localdate()
    for _ in range(3):
        try:
            with transaction.atomic():
                Doctor.objects.select_for_update().get(pk=doctor.pk)
                last = ClinicTicket.objects.filter(date=today, doctor=doctor).aggregate(n=Max("number"))["n"] or 0
                return ClinicTicket.objects.create(date=today, doctor=doctor, number=last + 1, priority=priority, patient=patient,
                                                   appointment=appointment, issued_by=user)
        except IntegrityError:
            continue
    raise ValidationError("صدور نوبت ناموفق بود؛ دوباره تلاش کنید.")


def check_in(appointment, user, priority=ClinicTicket.Priority.NORMAL):
    """بیمار نوبت‌دار امروز در پذیرش حاضر می‌شود؛ هر نوبت فقط یک شماره صف دارد."""
    if appointment.date != timezone.localdate() or appointment.status == Appointment.Status.CANCELLED:
        raise ValidationError("این نوبت برای امروز نیست یا لغو شده است.")
    existing = ClinicTicket.objects.filter(appointment=appointment).first()
    if existing:
        return existing
    return _issue(appointment.doctor, user, priority, appointment.patient, appointment)


def walk_in(doctor, user, identifier="", priority=ClinicTicket.Priority.NORMAL):
    """بیمار بدون نوبت قبلی: با کد ملی یا شماره پرونده به پرونده وصل می‌شود (اختیاری)."""
    patient = None
    identifier = normalize_digits(identifier or "").strip()
    if identifier:
        patient = Patient.objects.filter(national_code=identifier).first() or Patient.objects.filter(
            file_number__iexact=identifier).first()
        if patient is None:
            raise ValidationError("پرونده‌ای با این کد ملی یا شماره پرونده پیدا نشد؛ ابتدا پرونده بسازید یا خالی بگذارید.")
    return _issue(doctor, user, priority, patient)


def waiting(doctor, today=None):
    return ClinicTicket.objects.filter(date=today or timezone.localdate(), doctor=doctor, status=Status.WAITING).select_related(
        "patient", "appointment").order_by("-priority", "checked_in_at", "id")


def ahead_of(ticket):
    if ticket.status != Status.WAITING:
        return 0
    return sum(1 for t in waiting(ticket.doctor) if t.pk != ticket.pk and (
        t.priority > ticket.priority or (t.priority == ticket.priority and t.checked_in_at < ticket.checked_in_at)))


def current(doctor):
    return ClinicTicket.objects.filter(date=timezone.localdate(), doctor=doctor, status=Status.CALLED).select_related(
        "patient").order_by("-called_at").first()


def _close_current(doctor):
    ClinicTicket.objects.filter(date=timezone.localdate(), doctor=doctor, status=Status.CALLED).update(
        status=Status.DONE, finished_at=timezone.now())


@transaction.atomic
def call_next(doctor):
    Doctor.objects.select_for_update().get(pk=doctor.pk)
    ticket = lock_own_rows(waiting(doctor)).first()
    _close_current(doctor)
    if ticket is None:
        return None
    ticket.status, ticket.called_at = Status.CALLED, timezone.now()
    ticket.save(update_fields=["status", "called_at"])
    return ticket


def call_ticket(ticket):
    if ticket.date != timezone.localdate() or ticket.status not in (Status.WAITING, Status.SKIPPED):
        raise ValidationError("این نوبت قابل فراخوان نیست.")
    with transaction.atomic():
        _close_current(ticket.doctor)
        ticket.status, ticket.called_at = Status.CALLED, timezone.now()
        ticket.save(update_fields=["status", "called_at"])
    return ticket


def _require_called(ticket):
    if ticket.status != Status.CALLED:
        raise ValidationError("این نوبت در حال حاضر فراخوانده نشده است.")


def recall(ticket):
    _require_called(ticket)
    ticket.recall_count += 1
    ticket.called_at = timezone.now()
    ticket.save(update_fields=["recall_count", "called_at"])


def finish(ticket, status=Status.DONE):
    _require_called(ticket)
    ticket.status, ticket.finished_at = status, timezone.now()
    ticket.save(update_fields=["status", "finished_at"])


def cancel(ticket):
    if ticket.status != Status.WAITING:
        raise ValidationError("فقط نوبت در انتظار لغو می‌شود.")
    ticket.status, ticket.finished_at = Status.CANCELLED, timezone.now()
    ticket.save(update_fields=["status", "finished_at"])


def display_data(doctor_ids=None, lab_codes=None, history=6):
    """داده عمومی مانیتور: شماره، پزشک و اتاق. نام بیمار، کد ملی یا هیچ داده شخصی دیگری برگردانده نمی‌شود.

    lab_codes: اگر داده شود، فراخوان‌های صف آزمایشگاه همان ساختمان هم در همین مانیتور می‌آید ("*" یعنی همه).
    """
    today = timezone.localdate()
    doctors = Doctor.objects.filter(is_active=True)
    if doctor_ids:
        doctors = doctors.filter(pk__in=doctor_ids)
    called = ClinicTicket.objects.filter(date=today, doctor__in=doctors, called_at__isnull=False).exclude(
        status__in=[Status.WAITING, Status.CANCELLED]).select_related("doctor").order_by("-called_at")[: history + 4]
    calls = [{
        "key": f"c{t.pk}:{t.recall_count}", "ticket": t.label, "doctor": t.doctor.full_name, "room": t.doctor.room,
        "active": t.status == Status.CALLED, "at": timezone.localtime(t.called_at).strftime("%H:%M"),
    } for t in called]
    if lab_codes:
        from apps.lab.services import queue as lab_queue

        lab = lab_queue.display_data(None if lab_codes == ["*"] else lab_codes, history)
        calls += [{"key": f"l{c['key']}", "ticket": c["ticket"], "doctor": "آزمایشگاه", "room": c["counter"],
                   "active": c["active"], "at": c["at"]} for c in lab["calls"]]
        calls.sort(key=lambda c: c["at"], reverse=True)
    counts = dict(ClinicTicket.objects.filter(date=today, doctor__in=doctors, status=Status.WAITING)
                  .values_list("doctor").annotate(n=Count("id")))
    working = set(ClinicTicket.objects.filter(date=today, doctor__in=doctors).values_list("doctor", flat=True))
    active_doctors = [d for d in doctors if d.pk in working]
    return {
        "time": timezone.localtime().strftime("%H:%M"),
        "calls": calls[:history],
        "waiting": [{"name": d.full_name, "room": d.room, "count": counts.get(d.pk, 0)} for d in active_doctors],
    }

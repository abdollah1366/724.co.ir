"""گزارش مدیریتی درمانگاه: فقط آمار تجمعی، بدون نام بیمار."""

from django.db.models import Count, F, Q, Sum
from django.utils import timezone

from apps.appointments.models import Appointment

from .models import (
    ClinicTicket, Diagnosis, Encounter, EncounterService, PrescriptionItem, Prescription, RecordAccess, Reminder, TherapySession,
)


def _avg(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values)) if values else None


def build(start, end):
    encounters = Encounter.objects.filter(started_at__date__range=(start, end))
    appointments = Appointment.objects.filter(date__range=(start, end))
    by_doctor = encounters.values("provider__title", "provider__first_name", "provider__last_name").annotate(
        total=Count("id"), signed=Count("id", filter=Q(status=Encounter.Status.SIGNED)),
        patients=Count("patient", distinct=True)).order_by("-total")
    no_show = appointments.filter(date__lt=timezone.localdate()).exclude(status__in=["cancelled", "done"]).count()
    past = appointments.filter(date__lt=timezone.localdate()).exclude(status="cancelled").count()
    services = EncounterService.objects.filter(encounter__started_at__date__range=(start, end)).values(
        "service_code__code", "service_code__title").annotate(
        count=Sum("quantity"), amount=Sum(F("quantity") * F("unit_tariff"))).order_by("-count")[:15]
    diagnoses = Diagnosis.objects.filter(encounter__started_at__date__range=(start, end), icd__isnull=False).values(
        "icd__code", "icd__title_fa", "icd__title").annotate(count=Count("id")).order_by("-count")[:15]
    prescriptions = Prescription.objects.filter(issued_at__date__range=(start, end), status=Prescription.Status.ISSUED)
    rx_list = list(prescriptions.prefetch_related("items"))
    dispensed = sum(1 for rx in rx_list if rx.dispense_status in ("تحویل کامل", "تحویل ناقص"))
    generics = PrescriptionItem.objects.filter(prescription__in=prescriptions, generic__isnull=False).values(
        "generic__name", "generic__dosage_form", "generic__strength").annotate(count=Count("id")).order_by("-count")[:15]
    sessions = TherapySession.objects.filter(held_at__date__range=(start, end)).values("plan__discipline").annotate(
        total=Count("id"), attended=Count("id", filter=Q(attendance="attended")),
        no_show=Count("id", filter=Q(attendance="no_show"))).order_by("-total")
    tickets = ClinicTicket.objects.filter(date__range=(start, end)).select_related("doctor")
    queue = {}
    for t in tickets:
        row = queue.setdefault(t.doctor.full_name, {"total": 0, "waits": [], "skipped": 0})
        row["total"] += 1
        row["skipped"] += t.status == ClinicTicket.Status.SKIPPED
        if t.called_at:
            row["waits"].append(t.wait_minutes)
    reminders = Reminder.objects.filter(send_on__range=(start, end)).values("status").annotate(count=Count("id"))
    from .models.rehab import Discipline

    disciplines = dict(Discipline.choices)
    statuses = dict(Reminder.Status.choices)
    return {
        "encounters": encounters.count(), "signed": encounters.filter(status=Encounter.Status.SIGNED).count(),
        "patients": encounters.values("patient").distinct().count(), "by_doctor": by_doctor,
        "appointments": appointments.exclude(status="cancelled").count(), "no_show": no_show,
        "no_show_rate": round(no_show * 100 / past) if past else None,
        "services": services, "diagnoses": diagnoses,
        "prescriptions": len(rx_list), "dispensed": dispensed,
        "dispense_rate": round(dispensed * 100 / len(rx_list)) if rx_list else None, "generics": generics,
        "sessions": [{**s, "label": disciplines.get(s["plan__discipline"], s["plan__discipline"])} for s in sessions],
        "queue": [{"doctor": k, "total": v["total"], "skipped": v["skipped"], "avg_wait": _avg(v["waits"]),
                   "max_wait": max(v["waits"]) if v["waits"] else None} for k, v in queue.items()],
        "reminders": [{"label": statuses.get(r["status"], r["status"]), "count": r["count"]} for r in reminders],
        "emergency_access": RecordAccess.objects.filter(kind=RecordAccess.Kind.EMERGENCY, created_at__date__range=(start, end)).count(),
    }

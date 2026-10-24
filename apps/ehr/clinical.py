"""منطق ویزیت: شروع، امضا، یادداشت تکمیلی و خلاصه پایش بیماری‌های مزمن."""

import datetime

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.appointments.models import Appointment

from .models import ChronicProgram, Encounter, EncounterAddendum, Problem, VitalSigns

# اهداف رایج کنترل در بیماران سرپایی؛ پزشک می‌تواند برای هر بیمار هدف متفاوتی در برنامه درمان بنویسد.
BP_TARGET = (140, 90)
HBA1C_TARGET = 7.0
FBS_TARGET = 130
HBA1C_CODES = ("HBA1C",)
FBS_CODES = ("FBS",)


def start_encounter(patient, doctor, user, appointment=None, kind=Encounter.Kind.VISIT):
    if doctor is None:
        raise ValidationError("حساب شما به پزشک یا درمانگری وصل نیست.")
    if appointment is not None:
        if appointment.patient_id != patient.pk:
            raise ValidationError("نوبت متعلق به این بیمار نیست.")
        existing = Encounter.objects.filter(appointment=appointment).first()
        if existing:
            return existing
    with transaction.atomic():
        encounter = Encounter.objects.create(patient=patient, provider=doctor, appointment=appointment, kind=kind,
                                             created_by=user)
        if appointment is not None and appointment.status in (Appointment.Status.PENDING, Appointment.Status.CONFIRMED):
            appointment.status = Appointment.Status.CONFIRMED
            appointment.save(update_fields=["status", "updated_at"])
    return encounter


def can_write(user, encounter):
    """فقط پزشک صاحب ویزیت شرح حال، تشخیص و امضا را ثبت می‌کند."""
    doctor = getattr(user, "doctor", None)
    return encounter.is_open and doctor is not None and doctor.pk == encounter.provider_id


def sign(encounter, user):
    if not can_write(user, encounter) or not user.has_perm("ehr.sign_encounter"):
        raise ValidationError("فقط پزشک همین ویزیت می‌تواند آن را امضا کند.")
    if not (encounter.chief_complaint or encounter.assessment or encounter.diagnoses.exists()):
        raise ValidationError("پیش از امضا، شکایت اصلی، ارزیابی یا حداقل یک تشخیص را ثبت کنید.")
    with transaction.atomic():
        encounter.status = Encounter.Status.SIGNED
        encounter.signed_by = user
        encounter.signed_at = timezone.now()
        encounter._signing = True
        encounter.save()
        if encounter.appointment_id:
            Appointment.objects.filter(pk=encounter.appointment_id).update(
                status=Appointment.Status.DONE, updated_at=timezone.now()
            )
        from .engagement import visit_reminder_from_encounter

        visit_reminder_from_encounter(encounter, user)
    return encounter


def add_addendum(encounter, user, text):
    if encounter.is_open:
        raise ValidationError("ویزیت هنوز باز است؛ متن را مستقیم ویرایش کنید.")
    doctor = getattr(user, "doctor", None)
    if doctor is None or doctor.pk != encounter.provider_id:
        raise ValidationError("یادداشت تکمیلی را فقط پزشک همان ویزیت ثبت می‌کند.")
    return EncounterAddendum.objects.create(encounter=encounter, user=user, text=text)


def save_vitals(vitals):
    vitals.full_clean()
    vitals.save()
    return vitals


# ---------------------------------------------------------------- پایش بیماری مزمن


@dataclass
class Reading:
    when: object
    value: float
    label: str


def _lab_series(patient, codes):
    """جواب‌های تأییدشده آزمایشگاه ۷۲۴ و مقادیر برگه‌های بیرونی، به ترتیب زمان."""
    from apps.lab.models import LabResult

    from .models import ExternalLabValue

    readings = []
    lab_patient = getattr(patient, "lab_patient", None)
    if lab_patient is not None:
        results = LabResult.objects.filter(
            item__order__patient=lab_patient, item__test__code__in=codes, status=LabResult.Status.APPROVED,
            numeric_value__isnull=False,
        )
        readings += [Reading(r.approved_at, float(r.numeric_value), f"{r.numeric_value.normalize()}") for r in results]
    for v in ExternalLabValue.objects.filter(document__patient=patient, document__is_void=False, test__code__in=codes).select_related("document"):
        when = timezone.make_aware(datetime.datetime.combine(v.document.document_date, datetime.time(12)))
        readings.append(Reading(when, float(v.value), f"{v.value.normalize()} (بیرون)"))
    return sorted(readings, key=lambda r: r.when)


def chronic_summary(patient):
    programs = set(
        patient.problems.filter(status=Problem.Status.ACTIVE).exclude(chronic_program="").values_list("chronic_program", flat=True)
    )
    vitals = list(patient.vitals.order_by("recorded_at"))
    bp = [Reading(v.recorded_at, v.systolic, v.bp) for v in vitals if v.systolic]
    diastolic = {v.recorded_at: v.diastolic for v in vitals if v.systolic}
    weight = [Reading(v.recorded_at, float(v.weight), f"{v.weight}") for v in vitals if v.weight]
    glucose = [Reading(v.recorded_at, v.glucose, f"{v.glucose} ({v.get_glucose_kind_display()})") for v in vitals if v.glucose]
    hba1c = _lab_series(patient, HBA1C_CODES)
    fbs = _lab_series(patient, FBS_CODES)
    status = {}
    if bp:
        last = bp[-1]
        status["bp"] = last.value < BP_TARGET[0] and diastolic[last.when] < BP_TARGET[1]
    if hba1c:
        status["hba1c"] = hba1c[-1].value < HBA1C_TARGET
    latest = vitals[-1] if vitals else None
    series = {"bp": bp, "weight": weight, "glucose": glucose, "hba1c": hba1c, "fbs": fbs}
    return {
        "last": {key: values[-1] for key, values in series.items() if values},
        "programs": [ChronicProgram(p).label for p in programs], "program_keys": programs, "bp": bp, "weight": weight,
        "glucose": glucose, "hba1c": hba1c, "fbs": fbs, "status": status, "latest_vitals": latest,
        "bmi": next((v.bmi for v in reversed(vitals) if v.bmi), None),
    }


def sparkline(readings, width=220, height=48):
    """نقاط یک نمودار خطی ساده SVG (بدون کتابخانه خارجی)."""
    if len(readings) < 2:
        return ""
    values = [r.value for r in readings]
    low, high = min(values), max(values)
    span = (high - low) or 1
    step = width / (len(values) - 1)
    return " ".join(f"{i * step:.1f},{height - 4 - (v - low) / span * (height - 8):.1f}" for i, v in enumerate(values))


def registry(program):
    """بیماران دارای بیماری مزمن فعال با آخرین وضعیت کنترل."""
    problems = Problem.objects.filter(status=Problem.Status.ACTIVE, chronic_program=program).select_related("patient")
    rows = []
    for patient in {p.patient for p in problems}:
        summary = chronic_summary(patient)
        last_bp = summary["bp"][-1] if summary["bp"] else None
        last_a1c = summary["hba1c"][-1] if summary["hba1c"] else None
        last_visit = patient.encounters.filter(status=Encounter.Status.SIGNED).first()
        rows.append({
            "patient": patient, "bp": last_bp, "hba1c": last_a1c, "status": summary["status"],
            "last_visit": last_visit,
            "controlled": all(summary["status"].values()) if summary["status"] else None,
        })
    return sorted(rows, key=lambda r: (r["controlled"] is not False, r["patient"].last_name))


def latest_vitals_for_encounter(encounter):
    return VitalSigns.objects.filter(encounter=encounter).order_by("-recorded_at")

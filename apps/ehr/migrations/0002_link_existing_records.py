"""ساخت پرونده واحد برای بیماران آزمایشگاه و نوبت‌های قبلی و اتصال آن‌ها با کد ملی. چیزی حذف نمی‌شود."""

import jdatetime
from django.db import migrations
from django.utils import timezone


def _numberer(apps):
    sequence = apps.get_model("audit", "DocumentSequence")

    def next_number(date):
        key = f"P-{jdatetime.date.fromgregorian(date=date).year}"
        seq, _ = sequence.objects.get_or_create(key=key)
        seq.last_value += 1
        seq.save(update_fields=["last_value"])
        return f"{key}-{seq.last_value:05d}"

    return next_number


def link(apps, schema_editor):
    Patient = apps.get_model("ehr", "Patient")
    LabPatient = apps.get_model("lab", "LabPatient")
    Appointment = apps.get_model("appointments", "Appointment")
    next_number = _numberer(apps)
    today = timezone.localdate()

    for lp in LabPatient.objects.filter(patient__isnull=True).order_by("id"):
        lookup = {"national_code": lp.national_code} if lp.national_code else {"foreign_id": lp.foreign_id}
        patient = Patient.objects.filter(**lookup).first()
        if patient is None:
            patient = Patient.objects.create(
                file_number=next_number(today), first_name=lp.first_name, last_name=lp.last_name,
                father_name=lp.father_name, sex=lp.sex, birth_date=lp.birth_date, mobile=lp.mobile, phone=lp.phone,
                address=lp.address, is_verified=True, **lookup,
            )
        lp.patient = patient
        lp.save(update_fields=["patient"])

    for appointment in Appointment.objects.filter(patient__isnull=True).exclude(national_code="").order_by("id"):
        patient = Patient.objects.filter(national_code=appointment.national_code).first()
        if patient is None:
            parts = appointment.patient_full_name.split()
            first, last = (parts[0], " ".join(parts[1:])) if len(parts) > 1 else ("", " ".join(parts))
            patient = Patient.objects.create(
                file_number=next_number(today), national_code=appointment.national_code, first_name=first,
                last_name=last, mobile=appointment.mobile, is_verified=False,
            )
        appointment.patient = patient
        appointment.save(update_fields=["patient"])


def unlink(apps, schema_editor):
    apps.get_model("lab", "LabPatient").objects.update(patient=None)
    apps.get_model("appointments", "Appointment").objects.update(patient=None)
    apps.get_model("ehr", "Patient").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("ehr", "0001_initial"),
        ("audit", "0001_initial"),
        ("lab", "0006_labpatient_patient"),
        ("appointments", "0003_appointment_patient"),
    ]

    operations = [migrations.RunPython(link, unlink)]

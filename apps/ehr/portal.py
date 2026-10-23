"""پرونده سلامت من: بیمار با کد پیامکی به موبایل ثبت‌شده در پذیرش، پرونده خود را به حسابش وصل می‌کند و خلاصه آن را می‌بیند.

طبق منشور حقوق بیمار، بیمار حق دسترسی به اطلاعات پرونده خود را دارد؛ هر مشاهده در گزارش ممیزی ثبت می‌شود.
"""

import datetime
import secrets

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.sms import send_sms
from apps.lab.models.portal import hash_code
from apps.lab.services.portal import rate_limited  # noqa: F401  (همان محدودیت تلاش پورتال آزمایشگاه)

from . import engagement, pharmacy
from .models import ClinicTicket, Encounter, Patient, PortalCode, Prescription, Reminder


def patient_for(user):
    patient = Patient.objects.filter(user=user).first() if user.is_authenticated else None
    if patient is None and user.is_authenticated:
        # کسی که پرونده آزمایشگاهش را قبلاً با کد پیامکی وصل کرده، به همان پرونده سلامت هم وصل می‌شود.
        lab_patient = _lab_patient(user)
        if lab_patient and lab_patient.patient_id and not lab_patient.patient.user_id:
            patient = lab_patient.patient
            patient.user = user
            patient.save(update_fields=["user"])
    return patient


def _lab_patient(user):
    from apps.lab.models import LabPatient

    return LabPatient.objects.filter(user=user).select_related("patient").first()


def find(identifier):
    return Patient.objects.filter(national_code=identifier).first() or Patient.objects.filter(foreign_id=identifier).first()


def issue_code(patient, user, ip=""):
    """فقط پرونده‌ای که هویتش در پذیرش تأیید شده و موبایل دارد کد می‌گیرد."""
    if not (patient.is_verified and patient.mobile):
        return None
    last = PortalCode.objects.filter(patient=patient, user=user, used_at__isnull=True).first()
    if last and (timezone.now() - last.created_at).total_seconds() < settings.LAB_RULES["otp_resend_seconds"]:
        return last
    code = f"{secrets.randbelow(10**6):06d}"
    access = PortalCode.objects.create(
        patient=patient, user=user, code_hash=hash_code(code), ip=ip[:45],
        expires_at=timezone.now() + datetime.timedelta(seconds=settings.LAB_RULES["otp_ttl_seconds"]),
    )
    send_sms(patient.mobile, f"کد اتصال پرونده سلامت: {code}\nاین کد را به هیچ‌کس ندهید. {settings.CLINIC_NAME}", "ehr_otp")
    return access


def verify_and_link(access, user, code):
    if access is None or access.user_id != user.pk or access.used_at or access.expires_at < timezone.now():
        raise ValidationError("کد منقضی شده است؛ دوباره درخواست کنید.")
    if access.attempts >= settings.LAB_RULES["otp_max_attempts"]:
        raise ValidationError("تعداد تلاش‌ها بیش از حد مجاز است؛ دوباره درخواست کنید.")
    if not access.matches(code.strip()):
        access.attempts += 1
        access.save(update_fields=["attempts"])
        raise ValidationError("کد وارد شده درست نیست.")
    patient = access.patient
    if patient.user_id and patient.user_id != user.pk:
        raise ValidationError("این پرونده به حساب دیگری متصل است؛ با پذیرش درمانگاه تماس بگیرید.")
    if Patient.objects.filter(user=user).exclude(pk=patient.pk).exists():
        raise ValidationError("حساب شما قبلاً به پرونده دیگری متصل شده است.")
    access.used_at = timezone.now()
    access.save(update_fields=["used_at"])
    patient.user = user
    patient.save(update_fields=["user"])
    return patient


def summary(patient):
    today = timezone.localdate()
    ticket = ClinicTicket.objects.filter(patient=patient, date=today).exclude(
        status__in=[ClinicTicket.Status.DONE, ClinicTicket.Status.CANCELLED]).select_related("doctor").first()
    return {
        "ticket": ticket, "ahead": engagement.ahead_of(ticket) if ticket else 0,
        "appointments": patient.appointments.active().filter(date__gte=today).select_related("doctor").order_by("date", "time"),
        "reminders": patient.reminders.filter(status=Reminder.Status.PENDING, due_date__gte=today),
        "allergies": patient.allergies.filter(is_active=True),
        "problems": patient.problems.filter(status="active").select_related("icd"),
        "current_meds": pharmacy.current_medications(patient),
        "encounters": patient.encounters.filter(status=Encounter.Status.SIGNED).select_related("provider").prefetch_related(
            "diagnoses__icd")[:20],
        "prescriptions": patient.prescriptions.filter(status=Prescription.Status.ISSUED).select_related("prescriber").prefetch_related(
            "items__generic", "items__product")[:20],
        "plans": patient.treatment_plans.select_related("therapist").prefetch_related("sessions"),
        "documents": patient.documents.filter(is_void=False).prefetch_related("files")[:30],
        "lab_patient": getattr(patient, "lab_patient", None),
    }

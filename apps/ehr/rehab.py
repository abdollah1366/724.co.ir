"""منطق خدمات توانبخشی و روان: برنامه درمان، جلسه، امضا و ارزیابی‌های استاندارد."""

from collections import namedtuple

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.clinic.models import ProviderType
from apps.core import events

from .clinical import sparkline
from .models import Assessment, Discipline, Referral, TherapySession, TreatmentPlan
from .models.rehab import AUDIOGRAM_FREQUENCIES, CONFIDENTIAL_DISCIPLINES

# رشته پیش‌فرض برنامه بر اساس نوع درمانگر
DISCIPLINE_BY_PROVIDER = {
    ProviderType.PHYSIOTHERAPIST: Discipline.PHYSIO,
    ProviderType.SPEECH_THERAPIST: Discipline.SPEECH,
    ProviderType.OCCUPATIONAL_THERAPIST: Discipline.OT,
    ProviderType.PSYCHOLOGIST: Discipline.PSYCHOLOGY,
    ProviderType.AUDIOLOGIST: Discipline.AUDIOLOGY,
    ProviderType.NUTRITIONIST: Discipline.NUTRITION,
}

# ارزیابی‌های مناسب هر رشته (برای فرم)
ASSESSMENTS_BY_DISCIPLINE = {
    Discipline.PHYSIO: ["vas", "rom", "other"],
    Discipline.OT: ["vas", "rom", "other"],
    Discipline.SPEECH: ["speech", "other"],
    Discipline.PSYCHOLOGY: ["phq9", "gad7", "other"],
    Discipline.AUDIOLOGY: ["audiogram", "other"],
    Discipline.NUTRITION: ["nutrition", "other"],
}

Reading = namedtuple("Reading", "when value")

SCORE_RANGES = {"phq9": (0, 27), "gad7": (0, 21), "vas": (0, 10)}
HEARING_RANGE = (-10, 120)


def is_therapist(user, plan):
    doctor = getattr(user, "doctor", None)
    return doctor is not None and doctor.pk == plan.therapist_id


def can_write(user, plan):
    return plan.status == TreatmentPlan.Status.ACTIVE and is_therapist(user, plan)


@transaction.atomic
def create_plan(patient, therapist, user, **data):
    referral = data.get("referral")
    if referral is not None and referral.patient_id != patient.pk:
        raise ValidationError("ارجاع مربوط به این بیمار نیست.")
    plan = TreatmentPlan(patient=patient, therapist=therapist, created_by=user, **data)
    plan.full_clean()
    plan.save()
    if referral is not None and referral.status == Referral.Status.OPEN:
        referral.status = Referral.Status.DONE
        referral.save(update_fields=["status"])
    return plan


def add_session(plan, user, session):
    if not can_write(user, plan):
        raise ValidationError("فقط درمانگر همین برنامه فعال می‌تواند جلسه ثبت کند.")
    session.plan, session.created_by = plan, user
    if session.service_code_id:
        session.unit_tariff = session.service_code.tariff(session.sector)
    session.save()
    return session


def sign_session(session, user):
    if not is_therapist(user, session.plan):
        raise ValidationError("فقط درمانگر همین برنامه جلسه را امضا می‌کند.")
    if session.signed_at:
        raise ValidationError("این جلسه قبلاً امضا شده است.")
    if session.attendance == TherapySession.Attendance.ATTENDED and not (session.intervention or session.response):
        raise ValidationError("برای جلسه انجام‌شده، اقدامات درمانی یا پاسخ بیمار را بنویسید.")
    with transaction.atomic():
        session.sign()
        publish_session_signed(session)
    return session


def publish_session_signed(session):
    """رویداد «جلسه درمان انجام و امضا شد» برای صورتحساب؛ بدون رشته درمان، یادداشت یا پاسخ بیمار."""
    plan = session.plan
    events.publish("ehr.therapysession.signed", session, {
        "session_id": session.pk, "patient_id": plan.patient_id, "patient_name": plan.patient.full_name,
        "provider_id": plan.therapist_id, "provider_name": str(plan.therapist), "attendance": session.attendance,
        "date": str(timezone.localdate(session.held_at)), "code": session.service_code.code if session.service_code_id else "",
        "title": session.service_code.title[:150] if session.service_code_id else "", "unit_tariff": session.unit_tariff,
    })


def close_plan(plan, user, status, summary):
    if not is_therapist(user, plan) and not user.has_perm("ehr.view_all_records"):
        raise ValidationError("فقط درمانگر برنامه یا مسئول فنی برنامه را می‌بندد.")
    if plan.status != TreatmentPlan.Status.ACTIVE:
        raise ValidationError("این برنامه قبلاً بسته شده است.")
    if status not in (TreatmentPlan.Status.COMPLETED, TreatmentPlan.Status.DISCONTINUED):
        raise ValidationError("وضعیت نامعتبر است.")
    if len((summary or "").strip()) < 5:
        raise ValidationError("خلاصه پایان درمان را بنویسید.")
    plan.status, plan.discharge_summary = status, summary.strip()
    plan.save(update_fields=["status", "discharge_summary"])
    return plan


def audiogram_data(values):
    """values: {"right": {250: 20, ...}, "left": {...}} با اعداد dB HL؛ خالی‌ها حذف می‌شوند."""
    data = {}
    for ear in ("right", "left"):
        row = {}
        for freq in AUDIOGRAM_FREQUENCIES:
            value = values.get(ear, {}).get(freq)
            if value is None:
                continue
            if not HEARING_RANGE[0] <= value <= HEARING_RANGE[1]:
                raise ValidationError(f"آستانه {freq} هرتز باید بین {HEARING_RANGE[0]} و {HEARING_RANGE[1]} dB باشد.")
            row[str(freq)] = value
        if row:
            data[ear] = row
    if not data:
        raise ValidationError("دست‌کم یک آستانه شنوایی وارد کنید.")
    return data


def add_assessment(plan, user, kind, score=None, data=None, notes=""):
    if not can_write(user, plan):
        raise ValidationError("فقط درمانگر همین برنامه فعال می‌تواند ارزیابی ثبت کند.")
    if kind in SCORE_RANGES:
        low, high = SCORE_RANGES[kind]
        if score is None or not low <= score <= high:
            raise ValidationError(f"امتیاز باید بین {low} و {high} باشد.")
    return Assessment.objects.create(plan=plan, kind=kind, score=score, data=data or {}, notes=notes, created_by=user)


def default_share(discipline):
    return discipline not in CONFIDENTIAL_DISCIPLINES


def progress(plan):
    """روند درد جلسات و امتیاز ارزیابی‌های تکرارشونده برای نمودار کوچک."""
    pain = [Reading(s.held_at, s.pain_score) for s in plan.sessions.filter(pain_score__isnull=False).order_by("held_at")]
    scores = {}
    for a in plan.assessments.filter(score__isnull=False).order_by("assessed_at"):
        scores.setdefault(a.get_kind_display(), []).append(Reading(a.assessed_at, float(a.score)))
    series = {"شدت درد جلسات": pain, **scores}
    return [{"label": label, "readings": r, "points": sparkline(r)} for label, r in series.items() if r]

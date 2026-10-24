"""گردش نمونه پرونده سلامت: نوبت ← پذیرش و شماره صف ← فراخوان ← ویزیت و علائم حیاتی ← تشخیص ← نسخه ← امضا و یادآور
← ارجاع به فیزیوتراپی، شنوایی‌شناسی و روانشناسی ← جلسه درمان و ارزیابی.

با `python manage.py ehr_demo --password ...` اجرا می‌شود. همه داده‌ها با «(نمونه)» ساخته می‌شوند تا با داده واقعی اشتباه نشوند.
"""

import datetime

from django.utils import timezone

from apps.appointments.models import Appointment
from apps.audit.roles import setup_roles
from apps.backoffice.demo import _user
from apps.clinic.models import Department, Doctor, ProviderType

from . import clinical, engagement, pharmacy, rehab
from .models import (
    Allergy, Diagnosis, Encounter, GenericDrug, ICD10Code, Patient, Prescription, Problem, Referral, TherapySession, VitalSigns,
)

DEMO_SLUG = "demo-ehr-physician"


class DemoAlreadyRun(Exception):
    pass


def national_code(prefix):
    """کد ملی آزمایشی با رقم کنترل درست (برای داده نمونه)."""
    digits = [int(d) for d in f"{prefix:09d}"]
    remainder = sum(d * (10 - i) for i, d in enumerate(digits)) % 11
    return "".join(map(str, digits)) + str(remainder if remainder < 2 else 11 - remainder)


def _generic(name, form, strength, name_fa):
    generic = GenericDrug.objects.filter(name__istartswith=name, is_active=True).first()
    return generic or GenericDrug.objects.create(name=name, dosage_form=form, strength=strength, name_fa=name_fa,
                                                 needs_verification=True)


def _provider(user, slug, first, last, kind, room, code, council):
    department, _ = Department.objects.get_or_create(slug="clinic", defaults={"title": "درمانگاه", "kind": "clinic", "summary": "-"})
    return Doctor.objects.create(first_name=first, last_name=f"{last} (نمونه)", slug=slug, department=department, provider_type=kind,
                                 room=room, queue_code=code, medical_council_number=council, user=user,
                                 title="دکتر" if kind == ProviderType.PHYSICIAN else "")


def run(password=None):
    if Doctor.objects.filter(slug=DEMO_SLUG).exists():
        raise DemoAlreadyRun("گردش نمونه پرونده سلامت قبلاً روی این پایگاه داده اجرا شده است.")
    setup_roles()
    users = {
        key: _user(f"demo-ehr-{key}", f"{title} (نمونه)", [role], password=password)
        for key, title, role in [
            ("doctor", "پزشک", "پزشک"), ("nurse", "پرستار", "پرستار"), ("reception", "پذیرش", "پذیرش درمانگاه"),
            ("physio", "فیزیوتراپیست", "درمانگر توانبخشی"), ("audio", "شنوایی‌شناس", "درمانگر توانبخشی"),
            ("psych", "روانشناس", "روانشناس"), ("head", "مسئول فنی", "مسئول فنی درمانگاه"),
        ]
    }
    doc = _provider(users["doctor"], DEMO_SLUG, "سارا", "کریمی", ProviderType.PHYSICIAN, "اتاق ۱", "M", "DEMO-M-1")
    physio = _provider(users["physio"], "demo-ehr-physio", "حامد", "نوری", ProviderType.PHYSIOTHERAPIST, "اتاق ۴", "P", "DEMO-P-1")
    audio = _provider(users["audio"], "demo-ehr-audio", "نگار", "صالحی", ProviderType.AUDIOLOGIST, "اتاق ۵", "A", "DEMO-A-1")
    psych = _provider(users["psych"], "demo-ehr-psych", "مینا", "راد", ProviderType.PSYCHOLOGIST, "اتاق ۶", "S", "DEMO-S-1")
    now, today = timezone.now(), timezone.localdate()

    # پذیرش: بیمار نوبت اینترنتی دارد، هویتش تأیید و رضایت پیامکی ثبت می‌شود
    appointment = Appointment.objects.create(
        doctor=doc, date=today, time=datetime.time(10, 0), patient_full_name="زهرا احمدی (نمونه)",
        national_code=national_code(123456781), mobile="09120000001", consent_at=now, status=Appointment.Status.CONFIRMED,
    )
    patient = appointment.patient
    Patient.objects.filter(pk=patient.pk).update(
        first_name="زهرا", last_name="احمدی (نمونه)", sex="f", birth_date=datetime.date(1968, 3, 2), is_verified=True,
        reminder_consent_at=now, blood_group="A+",
    )
    patient.refresh_from_db()
    walk_in = engagement.walk_in(doc, users["reception"])
    ticket = engagement.check_in(appointment, users["reception"])
    engagement.call_next(doc)  # اولین نفر صف (بیمار بدون نوبت) فراخوانده و سپس بسته می‌شود
    called = engagement.call_next(doc)

    # ویزیت
    encounter = clinical.start_encounter(patient, doc, users["doctor"], appointment)
    VitalSigns.objects.create(patient=patient, encounter=encounter, recorded_by=users["nurse"], systolic=150, diastolic=94, pulse=82,
                              weight=84, height=162, glucose=168, glucose_kind="fasting")
    encounter.chief_complaint = "پیگیری قند و فشار خون، کمردرد و کاهش شنوایی"
    encounter.assessment = "دیابت نوع ۲ و پرفشاری خون کنترل‌نشده"
    encounter.plan = "رژیم کم‌نمک و کم‌قند، پیاده‌روی روزانه، آزمایش HbA1c سه ماه بعد"
    encounter.next_visit = today + datetime.timedelta(days=30)
    encounter.save()
    for code, primary in (("E11.9", True), ("I10", False)):
        icd = ICD10Code.objects.filter(code=code).first()
        Diagnosis.objects.create(encounter=encounter, icd=icd, is_primary=primary)
        Problem.objects.get_or_create(patient=patient, icd=icd, defaults={
            "title": "", "recorded_by": users["doctor"], "chronic_program": "diabetes" if code == "E11.9" else "hypertension"})
    Allergy.objects.create(patient=patient, kind="drug", substance="Penicillin", severity="severe", reaction="کهیر",
                           recorded_by=users["doctor"])
    prescription = Prescription.objects.create(patient=patient, prescriber=doc, encounter=encounter, created_by=users["doctor"])
    pharmacy.add_item(prescription, generic=_generic("Metformin", "Tablet", "500 mg", "متفورمین"), quantity=60,
                      dose="۱ عدد", frequency="روزی ۲ بار همراه غذا", duration_days=30)
    pharmacy.add_item(prescription, generic=_generic("Losartan", "Tablet", "50 mg", "لوزارتان"), quantity=30,
                      dose="۱ عدد", frequency="روزی ۱ بار", duration_days=30)
    prescription.issue(users["doctor"])
    referrals = {
        key: Referral.objects.create(patient=patient, encounter=encounter, from_provider=doc, to_provider=provider, reason=reason,
                                     created_by=users["doctor"])
        for key, provider, reason in [("physio", physio, "کمردرد مزمن"), ("audio", audio, "کاهش شنوایی دوطرفه"),
                                      ("psych", psych, "خلق پایین و بی‌خوابی")]
    }
    clinical.sign(encounter, users["doctor"])
    refill = engagement.refill_reminder(prescription, users["doctor"])

    # توانبخشی
    pt_plan = rehab.create_plan(patient, physio, users["physio"], discipline="physio", referral=referrals["physio"],
                                diagnosis="کمردرد مکانیکی", icd=ICD10Code.objects.filter(code="M54.5").first(),
                                goals="کاهش درد به کمتر از ۳ و بازگشت به پیاده‌روی ۳۰ دقیقه", planned_sessions=10,
                                frequency="۳ جلسه در هفته", start_date=today, share_with_physicians=True)
    for pain, held in ((7, now - datetime.timedelta(days=4)), (5, now - datetime.timedelta(days=2)), (3, now)):
        session = rehab.add_session(pt_plan, users["physio"], TherapySession(
            held_at=held, intervention="TENS، گرما و تمرین تقویت عضلات مرکزی", response="درد کمتر شد", pain_score=pain,
            home_program="تمرین پل و کشش همسترینگ روزی ۲ بار"))
        rehab.sign_session(session, users["physio"])
    audio_plan = rehab.create_plan(patient, audio, users["audio"], discipline="audiology", referral=referrals["audio"],
                                   diagnosis="کم‌شنوایی حسی-عصبی", icd=ICD10Code.objects.filter(code="H90.5").first(), goals="ارزیابی و تجویز سمعک", planned_sessions=2,
                                   start_date=today, share_with_physicians=True)
    rehab.add_assessment(audio_plan, users["audio"], "audiogram", data=rehab.audiogram_data({
        "right": {250: 20, 500: 25, 1000: 30, 2000: 40, 4000: 55, 8000: 60},
        "left": {250: 25, 500: 30, 1000: 35, 2000: 45, 4000: 60, 8000: 65}}))
    psych_plan = rehab.create_plan(patient, psych, users["psych"], discipline="psychology", referral=referrals["psych"],
                                   diagnosis="افسردگی خفیف تا متوسط", icd=ICD10Code.objects.filter(code="F32.9").first(), goals="بهبود خلق و خواب", planned_sessions=8,
                                   start_date=today, share_with_physicians=False)
    rehab.add_assessment(psych_plan, users["psych"], "phq9", score=12)
    session = rehab.add_session(psych_plan, users["psych"], TherapySession(intervention="(یادداشت محرمانه نمونه)"))
    rehab.sign_session(session, users["psych"])

    return {
        "users": users, "patient": patient, "encounter": encounter, "prescription": prescription, "ticket": ticket,
        "walk_in": walk_in, "called": called, "refill": refill, "plans": [pt_plan, audio_plan, psych_plan],
        "reminders": list(patient.reminders.all()),
        "psych_hidden": not psych_plan.can_read_notes(users["doctor"]),
        "encounter_signed": Encounter.objects.get(pk=encounter.pk).status == Encounter.Status.SIGNED,
    }

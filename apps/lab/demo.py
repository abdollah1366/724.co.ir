"""گردش کامل نمونه آزمایشگاه: نوبت اینترنتی ← نوبت حضوری و فراخوان ← پذیرش با بیمه پایه و تکمیلی ← دریافت
← نمونه‌گیری ← جواب و تأیید ← جواب آماده ← صورت‌حساب و تسویه بیمه ← مشاوره و نظرسنجی.

با `python manage.py lab_demo` اجرا می‌شود و همین تابع در آزمون خودکار بررسی می‌شود.
همه داده‌ها با «(نمونه)» ساخته می‌شوند تا با داده واقعی اشتباه نشوند.
"""

import datetime
from io import StringIO

from django.core.management import call_command
from django.utils import timezone

from apps.accounting import services as acc
from apps.accounting.models import Party
from apps.audit.roles import setup_roles
from apps.backoffice.demo import _user

from .models import (
    ClaimSettlement, ConsultRequest, Feedback, InsurancePlan, LabOrder, LabPanel, LabPatient, LabResult, LabTest,
    QueueCounter, QueueService, SamplingSchedule,
)
from .services import booking, crm, lis, queue
from .services import orders as order_service

DEMO_INSURER_CODE = "DEMO-LAB-TAMIN"


class DemoAlreadyRun(Exception):
    pass


def _schedules():
    if SamplingSchedule.objects.exists():
        return
    for weekday in range(7):
        if weekday == 4:  # جمعه
            continue
        SamplingSchedule.objects.create(kind="lab", weekday=weekday, start_time=datetime.time(7), end_time=datetime.time(11))
        SamplingSchedule.objects.create(kind="home", weekday=weekday, start_time=datetime.time(8), end_time=datetime.time(12),
                                        slot_minutes=30, capacity=1)


def run(password=None):
    if Party.objects.filter(code=DEMO_INSURER_CODE).exists():
        raise DemoAlreadyRun("گردش نمونه آزمایشگاه قبلاً روی این پایگاه داده اجرا شده است.")
    call_command("seed_lab", "--demo", stdout=StringIO())
    setup_roles()
    _schedules()
    today = timezone.localdate()
    users = {
        key: _user(f"demo-lab-{key}", f"{title} (نمونه)", [role], password=password)
        for key, title, role in [
            ("reception", "پذیرش", "پذیرش آزمایشگاه"), ("sampler", "نمونه‌گیر", "نمونه‌گیر آزمایشگاه"),
            ("tech", "کارشناس", "کارشناس آزمایشگاه"), ("director", "مسئول فنی", "مسئول فنی آزمایشگاه"),
            ("insurance", "کارشناس بیمه", "کارشناس بیمه آزمایشگاه"), ("manager", "مدیر", "مدیر آزمایشگاه"),
            ("consultant", "مشاور", "مشاور آزمایشگاه"), ("kiosk", "کیوسک", "کیوسک نوبت"),
        ]
    }

    # قراردادهای بیمه
    start = today - datetime.timedelta(days=365)
    tamin = Party.objects.create(kind=Party.Kind.INSURER, code=DEMO_INSURER_CODE, name="تأمین اجتماعی (نمونه)")
    dana = Party.objects.create(kind=Party.Kind.INSURER, code="DEMO-LAB-DANA", name="بیمه دانا (نمونه)")
    basic = InsurancePlan.objects.create(insurer=tamin, name="تأمین اجتماعی سرپایی (نمونه)", kind="basic", start_date=start,
                                         tariff_percent=60, coverage_percent=70)
    supp = InsurancePlan.objects.create(insurer=dana, name="دانا تکمیلی کارکنان (نمونه)", kind="supplementary", start_date=start,
                                        coverage_percent=80, max_per_order=20_000_000)

    # نوبت اینترنتی برای فردا و نوبت حضوری امروز
    future = [d for d in booking.available_dates("lab") if d > today]
    appointment = None
    if future:
        day = future[0]
        slot = booking.available_slots("lab", day)[0][0]
        appointment = booking.book("lab", day, slot, full_name="سارا محمدی (نمونه)", national_code="0013542419",
                                   mobile="09120000001")
    reception_q, sampling_q = QueueService.objects.get(code="A"), QueueService.objects.get(code="B")
    desk, room = QueueCounter.objects.filter(services=reception_q).first(), QueueCounter.objects.filter(services=sampling_q).first()
    ticket = queue.issue_ticket(reception_q)
    queue.call_next(desk, users["reception"])
    ticket.refresh_from_db()

    # پذیرش با بیمه پایه و تکمیلی
    patient = LabPatient.objects.create(
        national_code="0499370899", first_name="مریم", last_name="رضایی (نمونه)", sex="f",
        birth_date=datetime.date(1980, 5, 1), mobile="09120000002", reminder_consent_at=timezone.now(),
    )
    order = LabOrder.objects.create(
        patient=patient, created_by=users["reception"], basic_plan=basic, basic_insurance_no="12345678",
        supplementary_plan=supp, supplementary_insurance_no="D-998", referring_doctor="دکتر نمونه", referring_council_no="12345",
    )
    order_service.set_order_tests(order, list(LabTest.objects.filter(code__in=["FBS", "HBA1C", "TSH"])),
                                  list(LabPanel.objects.filter(code="LIPID")))
    order.post(users["reception"])
    order.refresh_from_db()
    payment = order_service.receive(order, users["reception"], order.net_patient, "card", "POS-1")
    queue.transfer(ticket, sampling_q)
    queue.call_next(room, users["sampler"])

    # نمونه‌گیری، جواب و تأیید
    for specimen in order.specimens.all():
        lis.collect_specimen(specimen, users["sampler"])
    values = {"FBS": "92", "HBA1C": "5.4", "TSH": "2.1", "CHOL": "185", "TG": "140", "HDL": "48", "LDL": "110"}
    for result in LabResult.objects.filter(item__order=order).select_related("item__test"):
        lis.enter_result(result, values.get(result.item.test.code, "1"), users["tech"])
    for result in LabResult.objects.filter(item__order=order):
        lis.approve_result(result, users["director"])
    order.refresh_from_db()

    # صورت‌حساب بیمه و تسویه پایه با کسورات
    claims = [order_service.build_claim(plan, today, today, users["insurance"]) for plan in (basic, supp)]
    for claim in claims:
        claim.post(users["insurance"])
    settlement = ClaimSettlement.objects.create(
        claim=claims[0], received=claims[0].total - 100_000, deductions=100_000, reference_no="SATNA-1", created_by=users["insurance"]
    )
    settlement.post(users["insurance"])

    # مشاوره و نظرسنجی
    consult = ConsultRequest.objects.create(
        topic="contract", full_name="شرکت نمونه", mobile="09120000003", message="قرارداد چکاپ سالانه کارکنان",
        consent_at=timezone.now(),
    )
    crm.log_interaction(consult, "call_out", "تعرفه سازمانی و نمونه‌گیری در محل توضیح داده شد.", users["consultant"],
                        next_follow_up=timezone.now() + datetime.timedelta(days=2))
    feedback = Feedback.objects.create(order=order, sent_at=timezone.now())
    crm.submit_feedback(feedback, {"overall": 5, "waiting": 4, "staff": 5, "sampling": 5, "comment": "", "wants_contact": False})

    _, trial = acc.trial_balance()
    return {
        "users": users, "order": order, "payment": payment, "claims": claims, "settlement": settlement,
        "appointment": appointment, "ticket": ticket, "consult": consult, "trial_totals": trial,
        "ar_insurers": {r["party__name"]: r["balance"] for r in acc.party_balances(kind="insurer")},
    }

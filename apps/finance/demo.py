"""گردش نمونه کامل مالی با داده ساختگی (python manage.py finance_demo؛ فقط روی پایگاه داده آزمایشی).

صورتحساب با تخفیف و سهم بیمه، دریافت ترکیبی و ناقص، علی‌الحساب، استرداد با تأیید نفر دوم، قرارداد پزشک با تأیید،
تسویه دومرحله‌ای، وام و حقوق ماهانه با نرخ‌های نمونه (برچسب «نمونه» دارند و نرخ قانونی نیستند) و در پایان کنترل مغایرت.
بیمار نمونه یک کاربر ساختگی است و هیچ پرونده سلامتی ساخته یا خوانده نمی‌شود.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.utils import timezone

from apps.accounting import services as acc
from apps.accounting.models import CostCenter, Party
from apps.audit.roles import setup_roles

from . import reconcile
from .models import (Contract, ContractRule, EmployeeLoan, Invoice, PayItemType, PayrollProfile, ProfilePayItem,
                     TenderMethod)
from .services import billing, contracts, payroll, settlements

DEMO_REF = "DEMO-FIN"
DEMO_PROVIDER_ID = 990001  # شناسه ساختگی؛ به پزشک واقعی متصل نیست


class DemoAlreadyRun(Exception):
    pass


def _user(username, role, password):
    User = get_user_model()
    user, created = User.objects.get_or_create(username=username, defaults={"is_staff": True, "first_name": "نمونه",
                                                                              "last_name": role})
    if created:
        user.set_password(password) if password else user.set_unusable_password()
        user.save()
    if role:
        user.groups.add(Group.objects.get(name=role))
    return User.objects.get(pk=user.pk)  # پاک شدن کش مجوزها


def run(password=None):
    if Invoice.objects.filter(source_ref=DEMO_REF).exists():
        raise DemoAlreadyRun("گردش نمونه مالی قبلاً اجرا شده است.")
    setup_roles()
    cashier = _user("demo_fin_cashier", "صندوق‌دار", password)
    accountant = _user("demo_fin_accountant", "حسابدار", password)
    manager = _user("demo_fin_manager", "مدیر مالی", password)
    approver = _user("demo_fin_approver", "تأییدکننده پرداخت", password)
    hr = _user("demo_fin_hr", "مسئول منابع انسانی", password)
    patient = _user("demo_fin_patient", "", password)
    patient.is_staff = False
    patient.save(update_fields=["is_staff"])
    employee = _user("demo_fin_employee", "", password)
    today = timezone.localdate()
    docs = {}

    # ---- قرارداد پزشک: ۴۰٪ صورتحساب درمانگاه، کف ماهانه ۵٬۰۰۰٬۰۰۰ ریال
    provider = Party.objects.create(kind=Party.Kind.PROVIDER, name="دکتر نمونه (ساختگی)")
    start, _ = settlements.month_bounds(*_jalali_month(today))
    contract = Contract.objects.create(kind=Contract.Kind.PROVIDER, title="قرارداد نمونه سهم پزشک", party=provider,
                                       provider_id=DEMO_PROVIDER_ID, provider_name=provider.name, start_date=start,
                                       created_by=hr)
    version = contracts.new_version(contract, hr, effective_from=start)
    version.period_floor = 5_000_000
    version.save()
    ContractRule.objects.create(version=version, module=Invoice.Module.CLINIC, basis=ContractRule.Basis.PERCENT_BILLED,
                                percent=Decimal("40"))
    contracts.submit_version(version, hr)
    contracts.approve_version(version, manager)
    contracts.set_status(contract, Contract.Status.ACTIVE, manager, "شروع همکاری نمونه")
    docs["contract"] = contract

    # ---- صورتحساب: ویزیت + خدمت، تخفیف با علت، سهم بیمه
    insurer = Party.objects.create(kind=Party.Kind.INSURER, name="بیمه نمونه (ساختگی)")
    invoice = billing.create_invoice(
        patient_user_id=patient.pk, patient_name="بیمار نمونه", provider_id=DEMO_PROVIDER_ID, provider_name=provider.name,
        source_ref=DEMO_REF, insurer=insurer, insurance_ref="DEMO-INS-1", discount_reason="تخفیف نمونه کارکنان",
        user=manager, lines=[
            {"description": "ویزیت", "service_code": "VISIT", "unit_price": 5_000_000, "discount": 500_000},
            {"description": "خدمت سرپایی نمونه", "service_code": "DEMO1", "unit_price": 3_000_000, "insurer_share": 2_000_000},
        ],
    )
    docs["invoice"] = invoice

    # ---- دریافت ترکیبی و ناقص (نقد + کارت)، سپس انتقال بانکی با مازاد علی‌الحساب
    first = billing.receive(patient_user_id=patient.pk, patient_name="بیمار نمونه", user=cashier, invoices=[invoice],
                            idempotency_key="demo-fin-receipt-1", tenders=[
                                {"method": TenderMethod.CASH, "amount": 2_000_000},
                                {"method": TenderMethod.CARD, "amount": 2_000_000, "reference_no": "DEMO-POS-1"}])
    again = billing.receive(patient_user_id=patient.pk, patient_name="بیمار نمونه", user=cashier, invoices=[invoice],
                            idempotency_key="demo-fin-receipt-1", tenders=[{"method": TenderMethod.CASH, "amount": 2_000_000}])
    invoice.refresh_from_db()
    second = billing.receive(patient_user_id=patient.pk, patient_name="بیمار نمونه", user=cashier,
                             invoices=list(billing.open_invoices(patient_user_id=patient.pk)),
                             tenders=[{"method": TenderMethod.BANK, "amount": 3_000_000, "reference_no": "DEMO-TRF-1"}])
    docs["receipts"] = [first, second]

    # ---- استرداد بخشی از علی‌الحساب: درخواست صندوق‌دار، تأیید نفر دوم، پرداخت صندوق‌دار
    refund = billing.request_refund(patient_user_id=patient.pk, patient_name="بیمار نمونه", amount=1_000_000,
                                    method=TenderMethod.BANK, reference_no="DEMO-RFD-1", reason="انصراف از خدمت نمونه",
                                    user=cashier)
    billing.approve_refund(refund, approver)
    refund.refresh_from_db()
    refund.post(cashier)
    docs["refund"] = refund

    # ---- تسویه ماه جاری پزشک: محاسبه حسابدار، تأیید مالی مدیر مالی، تأیید مدیریتی تأییدکننده، ثبت
    year, month = _jalali_month(today)
    settlement = settlements.create_settlement(contract, year, month, accountant)
    settlements.approve_finance(settlement, manager)
    settlements.approve_manager(settlement, approver)
    settlement.refresh_from_db()
    settlement.post(manager)
    docs["settlement"] = settlement

    # ---- حقوق: پرونده کارمند، قلم ثابت، وام قسطی، محاسبه منابع انسانی، تأیید و ثبت مدیر مالی
    staff_party = Party.objects.create(kind=Party.Kind.EMPLOYEE, name="کارمند نمونه (ساختگی)")
    profile = PayrollProfile.objects.create(party=staff_party, user=employee, personnel_no="DEMO-001", job_title="کارشناس نمونه",
                                            cost_center=CostCenter.objects.get(code="ADMIN"), base_salary=150_000_000,
                                            hired_on=start)
    ProfilePayItem.objects.create(profile=profile, item_type=PayItemType.objects.get(code="HOUSING"), amount=9_000_000)
    loan = EmployeeLoan.objects.create(profile=profile, amount=30_000_000, installment=10_000_000, method="bank",
                                       reference_no="DEMO-LON-1", created_by=hr)
    loan.post(manager)
    run = payroll.create_run(year, month, hr)
    payroll.approve_run(run, manager)
    run.refresh_from_db()
    run.post(manager)
    docs["loan"], docs["payroll"] = loan, run

    invoice.refresh_from_db()
    _, trial = acc.trial_balance()
    return {
        "docs": docs, "users": [cashier, accountant, manager, approver, hr, patient, employee],
        "idempotent_receipt": again.pk == first.pk, "invoice": invoice,
        "advance": billing.advance_balance(patient_user_id=patient.pk), "settlement_total": settlement.total,
        "payroll_net": sum(line.net for line in run.lines.all()), "trial": trial, "checks": reconcile.run_checks(),
    }


def _jalali_month(date):
    import jdatetime

    j = jdatetime.date.fromgregorian(date=date)
    return j.year, j.month

"""محاسبه حقوق ماهانه از روی اقلام قابل تنظیم، تأیید مستقل و ثبت سند تعهد حقوق.

نرخ‌ها از جدول‌های پنل خوانده می‌شوند. اگر قلمی فعال باشد ولی نرخ یا جدولش تعریف نشده باشد، محاسبه با پیام روشن
متوقف می‌شود؛ هیچ نرخ پیش‌فرض قانونی حدس زده نمی‌شود.
"""

from decimal import Decimal

import jdatetime
from django.conf import settings
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.accounting import services as acc
from apps.audit import log
from apps.audit.documents import DocStatus, DocumentError
from apps.audit.models import AuditLog

from ..models import EmployeeLoan, PayItemType, PayrollItem, PayrollLine, PayrollProfile, PayrollRun, TaxBracket
from ..money import percent_of, prorate, to_rial
from .settlements import month_bounds

Calc = PayItemType.Calc
ItemKind = PayItemType.Kind
MANUAL_FIELDS = {"BONUS": "bonus", "COMMISSION": "commission", "ADVANCE": "advance_deduction", "OTHER_DED": "other_deduction"}


def days_per_month():
    return settings.PAYROLL_RULES["days_per_month"]


def _active_profiles(start, end):
    return PayrollProfile.objects.filter(is_active=True, hired_on__lte=end).filter(Q(left_on__isnull=True) | Q(left_on__gte=start))


def _default_work_days(profile, start, end):
    first = max(start, profile.hired_on)
    last = min(end, profile.left_on or end)
    covered = (last - first).days + 1
    month_days = (end - start).days + 1
    return days_per_month() if covered >= month_days else min(covered, days_per_month())


def loan_remaining(loan, exclude_run=None):
    paid = PayrollItem.objects.filter(loan=loan, run__status=DocStatus.POSTED)
    if exclude_run is not None:
        paid = paid.exclude(run=exclude_run)
    return loan.amount - (paid.aggregate(s=Sum("amount"))["s"] or 0)


def monthly_tax(taxable, year):
    brackets = list(TaxBracket.objects.filter(year=year).order_by("from_amount"))
    if not brackets:
        raise DocumentError(f"جدول مالیات حقوق سال {year} تعریف نشده است (یا قلم مالیات را غیرفعال کنید).")
    tax = Decimal(0)
    for bracket in brackets:
        upper = bracket.to_amount if bracket.to_amount is not None else taxable
        portion = max(0, min(taxable, upper) - bracket.from_amount)
        tax += Decimal(portion) * bracket.rate / Decimal(100)
    return to_rial(tax)


def _require_rate(item_type):
    if item_type.rate is None:
        raise DocumentError(f"نرخ قلم «{item_type.name}» تعریف نشده است؛ آن را در اقلام حقوق وارد یا غیرفعال کنید.")
    return item_type.rate


def compute_line(line, types, year, run=None):
    """محاسبه یک ردیف. خروجی: (فهرست (قلم، مبلغ، جزئیات، وام)، جمع‌ها)."""
    profile = line.profile
    dpm = days_per_month()
    paid_days = max(0, min(line.work_days, dpm) - line.absence_days)
    fixed = {item.item_type_id: item.amount for item in profile.fixed_items.all()}
    items, earnings = [], []

    def add(item_type, amount, detail, loan=None):
        if amount > 0:
            items.append((item_type, int(amount), detail[:200], loan))

    for t in types:
        if t.kind != ItemKind.EARNING:
            continue
        if t.calc == Calc.BASE:
            add(t, prorate(profile.base_salary, paid_days, dpm), f"{profile.base_salary:,} × {paid_days}/{dpm} روز")
        elif t.calc == Calc.PROFILE and t.pk in fixed:
            add(t, prorate(fixed[t.pk], paid_days, dpm), f"{fixed[t.pk]:,} × {paid_days}/{dpm} روز")
        elif t.calc == Calc.OVERTIME and line.overtime_hours:
            rate = _require_rate(t)
            if not profile.monthly_hours:
                raise DocumentError(f"ساعت کار موظف ماهانه {profile} تعریف نشده است.")
            hourly = Decimal(profile.base_salary) / Decimal(profile.monthly_hours)
            add(t, to_rial(hourly * Decimal(line.overtime_hours) * rate), f"{line.overtime_hours} ساعت × {to_rial(hourly):,} × {rate}")
        elif t.calc == Calc.MANUAL and t.code in MANUAL_FIELDS:
            add(t, getattr(line, MANUAL_FIELDS[t.code]), "ورود دستی")
    earnings = list(items)
    gross = sum(amount for _, amount, _, _ in earnings)
    insurable = sum(amount for t, amount, _, _ in earnings if t.insurable)
    taxable = sum(amount for t, amount, _, _ in earnings if t.taxable)
    if line.overtime_hours and not any(t.calc == Calc.OVERTIME for t, *_ in earnings):
        raise DocumentError("برای اضافه‌کاری، قلم اضافه‌کاری فعال با ضریب تعریف کنید.")

    # کسور و سهم کارفرما به ترتیب: درصدی‌ها، سپس مالیات (پس از معافیت‌ها)، سپس وام و کسور دستی
    for t in types:
        if t.kind == ItemKind.EARNING:
            continue
        if t.kind == ItemKind.EMPLOYER and not t.liability_account_id:
            raise DocumentError(f"حساب بدهی سهم کارفرمای «{t.name}» تعریف نشده است.")
        if t.calc == Calc.PCT_INSURABLE:
            add(t, percent_of(insurable, _require_rate(t)), f"{insurable:,} × {t.rate}٪")
        elif t.calc == Calc.PCT_GROSS:
            add(t, percent_of(gross, _require_rate(t)), f"{gross:,} × {t.rate}٪")
    taxable -= sum(amount for t, amount, _, _ in items if t.kind == ItemKind.DEDUCTION and t.reduces_taxable)
    taxable = max(taxable, 0)
    for t in types:
        if t.kind == ItemKind.DEDUCTION and t.calc == Calc.TAX:
            add(t, monthly_tax(taxable, year), f"جدول مالیات {year} روی {taxable:,}")
    for t in types:
        if t.kind == ItemKind.DEDUCTION and t.calc == Calc.MANUAL and t.code in MANUAL_FIELDS:
            add(t, getattr(line, MANUAL_FIELDS[t.code]), "ورود دستی")
    loan_type = next((t for t in types if t.calc == Calc.LOAN), None)
    deductions_so_far = sum(amount for t, amount, _, _ in items if t.kind == ItemKind.DEDUCTION)
    room = gross - deductions_so_far
    if loan_type:
        for loan in EmployeeLoan.objects.filter(profile=profile, status=DocStatus.POSTED).order_by("date", "id"):
            remaining = loan_remaining(loan, exclude_run=run)
            amount = min(loan.installment, remaining, max(room, 0))
            if amount > 0:
                add(loan_type, amount, f"قسط {loan.number}؛ مانده پس از کسر {remaining - amount:,}", loan)
                room -= amount
    deductions = sum(amount for t, amount, _, _ in items if t.kind == ItemKind.DEDUCTION)
    employer = sum(amount for t, amount, _, _ in items if t.kind == ItemKind.EMPLOYER)
    net = gross - deductions
    if net < 0:
        raise DocumentError(f"کسور {profile} بیشتر از حقوق ناخالص است.")
    totals = {"gross": gross, "insurable": insurable, "taxable": taxable, "deductions": deductions, "employer_cost": employer,
              "net": net}
    return items, totals


@transaction.atomic
def calculate_run(run, user):
    run = PayrollRun.objects.select_for_update().get(pk=run.pk)
    if run.status != DocStatus.DRAFT:
        raise DocumentError("فقط لیست حقوق پیش‌نویس محاسبه می‌شود.")
    start, end = month_bounds(run.year, run.month)
    existing = {line.profile_id: line for line in run.lines.all()}
    for profile in _active_profiles(start, end):
        if profile.pk not in existing:
            existing[profile.pk] = PayrollLine.objects.create(run=run, profile=profile,
                                                              work_days=_default_work_days(profile, start, end))
    types = list(PayItemType.objects.filter(is_active=True).order_by("order", "code"))
    run.items.all().delete()
    for line in run.lines.select_related("profile").prefetch_related("profile__fixed_items"):
        items, totals = compute_line(line, types, run.year, run)
        PayrollItem.objects.bulk_create(PayrollItem(run=run, line=line, item_type=t, amount=a, detail=d, loan=loan)
                                        for t, a, d, loan in items)
        PayrollLine.objects.filter(pk=line.pk).update(**totals)
    PayrollRun.objects.filter(pk=run.pk).update(calculated_at=timezone.now(), approved_by=None, approved_at=None, date=end)
    run.refresh_from_db()
    log.record(AuditLog.Action.STATUS, run, message=f"محاسبه حقوق {run.year}/{run.month:02d}", user=user)
    return run


@transaction.atomic
def create_run(year, month, user):
    start, end = month_bounds(year, month)
    run = PayrollRun.objects.create(year=year, month=month, created_by=user, date=end)
    return calculate_run(run, user)


@transaction.atomic
def approve_run(run, user):
    if not user.has_perm("finance.approve_payrollrun"):
        raise DocumentError("مجوز تأیید لیست حقوق ندارید.")
    run = PayrollRun.objects.select_for_update().get(pk=run.pk)
    if run.status != DocStatus.DRAFT or not run.calculated_at:
        raise DocumentError("لیست حقوق باید پیش‌نویس و محاسبه‌شده باشد.")
    if settings.FINANCE_RULES["four_eyes"] and run.created_by_id == user.pk:
        raise DocumentError("ایجادکننده لیست حقوق نمی‌تواند خودش آن را تأیید کند.")
    PayrollRun.objects.filter(pk=run.pk).update(approved_by=user, approved_at=timezone.now())
    log.record(AuditLog.Action.STATUS, run, message="تأیید لیست حقوق", user=user)
    run.refresh_from_db()
    return run


def validate_run_post(run):
    if not run.approved_at or not run.calculated_at or run.approved_at < run.calculated_at:
        raise DocumentError("لیست حقوق پس از آخرین محاسبه تأیید نشده است.")
    if not run.lines.exists():
        raise DocumentError("لیست حقوق ردیف ندارد.")
    for line in run.lines.all():
        items = line.items.select_related("item_type")
        deductions = sum(i.amount for i in items if i.item_type.kind == ItemKind.DEDUCTION)
        gross = sum(i.amount for i in items if i.item_type.kind == ItemKind.EARNING)
        if gross != line.gross or gross - deductions != line.net:
            raise DocumentError(f"جمع اقلام {line.profile} با ردیف یکی نیست؛ دوباره محاسبه کنید.")


def post_run(run, user):
    totals = {}

    def add(code, party, center, debit, credit, text):
        key = (code, party.pk if party else None, center)
        row = totals.setdefault(key, [code, 0, 0, party, text, center])
        row[1] += debit
        row[2] += credit

    for line in run.lines.select_related("profile__party", "profile__cost_center"):
        party, center = line.profile.party, line.profile.cost_center.code
        for item in line.items.select_related("item_type__account", "item_type__liability_account"):
            t = item.item_type
            if t.kind == ItemKind.EARNING:
                add(t.account.code, None, center, item.amount, 0, t.name)
            elif t.kind == ItemKind.EMPLOYER:
                add(t.account.code, None, center, item.amount, 0, t.name)
                add((t.liability_account or t.account).code, None, None, 0, item.amount, t.name)
            else:
                needs_party = t.account.code == acc.EMPLOYEE_LOANS
                add(t.account.code, party if needs_party else None, None, 0, item.amount, t.name)
        add(acc.SALARIES_PAYABLE, party, None, 0, line.net, "خالص حقوق")
    text = f"حقوق {run.year}/{run.month:02d} ({run.number})"
    lines = [tuple(row) for row in totals.values()]
    acc.post_entry(acc.source_of(run), lines, text, run.date, user, cost_center="")


# ---------------------------------------------------------------- وام
def validate_loan(loan):
    if not loan.profile.is_active:
        raise DocumentError("پرونده کارمند غیرفعال است.")
    if loan.method != "cash" and not loan.reference_no.strip():
        raise DocumentError("برای پرداخت بانکی شماره پیگیری الزامی است.")


def post_loan(loan, user):
    money = loan.money_account.code if loan.money_account_id else (acc.CASH if loan.method == "cash" else acc.BANK)
    text = f"وام {loan.number} {loan.profile.party.name}"
    acc.post_entry(acc.source_of(loan), [(acc.EMPLOYEE_LOANS, loan.amount, 0, loan.profile.party, text),
                                         (money, 0, loan.amount, None, text)], text, loan.date, user, cost_center="")


def validate_loan_reverse(loan):
    if PayrollItem.objects.filter(loan=loan, run__status=DocStatus.POSTED).exists():
        raise DocumentError("از این وام قسط کسر شده است؛ برگشت آن ممکن نیست.")


def payslips_for(user):
    profile = getattr(user, "payroll_profile", None)
    if profile is None:
        return PayrollLine.objects.none()
    return PayrollLine.objects.filter(profile=profile, run__status=DocStatus.POSTED).select_related("run").order_by("-run__year", "-run__month")


def jalali_today():
    return jdatetime.date.fromgregorian(date=timezone.localdate())

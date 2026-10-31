"""ثبت اسناد حسابداری دوطرفه و گزارش‌های مالی."""

from collections import defaultdict

from django.conf import settings
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.audit.documents import DocumentError, next_number

from .models import AccountingPeriod, Account, CostCenter, JournalEntry, JournalLine, Party, PaymentMethod

# کد حساب‌های سیستمی (در مهاجرت 0002 ساخته می‌شوند)
CASH = "1101"
BANK = "1102"
AR_CUSTOMERS = "1103"
AR_INSURERS = "1104"
INVENTORY = "1105"
VAT_INPUT = "1106"
EMPLOYEE_LOANS = "1107"
AR_PATIENTS = "1108"
AP_SUPPLIERS = "2101"
GRNI = "2102"
VAT_OUTPUT = "2103"
AP_OTHER = "2104"
PATIENT_ADVANCES = "2105"
AP_PROVIDERS = "2106"
SALARIES_PAYABLE = "2107"
INSURANCE_PAYABLE = "2108"
PAYROLL_TAX_PAYABLE = "2109"
OPENING_EQUITY = "3102"
RETAINED_EARNINGS = "3103"
SALES = "4101"
SALES_RETURNS = "4102"
INVENTORY_GAIN = "4104"
COGS = "5101"
PURCHASE_VARIANCE = "5102"
INVENTORY_LOSS = "5103"
COST_ADJUSTMENT = "5104"
CASH_OVER_SHORT = "6105"
CONSUMPTION = "6110"  # مصرف داخلی دارو و لوازم در بخش‌ها (اتاق پانسمان، کلینیک، ...)

METHOD_ACCOUNT = {
    PaymentMethod.CASH: CASH,
    PaymentMethod.CARD: BANK,
    PaymentMethod.BANK: BANK,
    PaymentMethod.CHEQUE: BANK,
}


def source_of(doc):
    return doc._meta.label_lower, doc.pk, getattr(doc, "number", "") or ""


def account(code):
    return Account.objects.get(code=code)


# حساب‌هایی که زیردفتر عملیاتی دارند؛ سند دستی روی آن‌ها مجاز نیست تا دفتر کل با کاردکس و زیردفتر بیماران برابر بماند.
CONTROLLED_ACCOUNTS = {INVENTORY, GRNI, AR_PATIENTS, PATIENT_ADVANCES}


def check_period_open(date):
    period = AccountingPeriod.objects.filter(start_date__lte=date, end_date__gte=date).first()
    if period and period.status == AccountingPeriod.Status.CLOSED:
        raise DocumentError(f"دوره مالی «{period.name}» بسته است؛ سند با تاریخ {date} ثبت نمی‌شود.")
    if period is None and settings.FINANCE_RULES.get("require_open_period"):
        raise DocumentError(f"برای تاریخ {date} دوره مالی تعریف نشده است.")


def cost_center_for_source(source_type):
    """مرکز درآمد پیش‌فرض بر اساس ماژول منبع سند (FINANCE_COST_CENTER_BY_SOURCE)."""
    mapping = settings.FINANCE_COST_CENTER_BY_SOURCE
    code = mapping.get(source_type) or mapping.get(source_type.split(".")[0])
    return code


def _cost_centers(codes):
    codes = {c for c in codes if c}
    if not codes:
        return {}
    found = {c.code: c for c in CostCenter.objects.filter(code__in=codes)}
    missing = codes - set(found)
    if missing:
        raise DocumentError(f"مرکز هزینه تعریف نشده: {', '.join(sorted(missing))}")
    return found


def post_entry(source, lines, description, date, user, reverses=None, cost_center=None):
    """lines: فهرست (کد حساب، بدهکار، بستانکار، طرف حساب، شرح[، کد مرکز هزینه]). سطرهای صفر حذف و تراز بررسی می‌شود.

    cost_center: مرکز هزینه کل سند؛ اگر خالی باشد از نوع منبع تعیین می‌شود. ستون ششم هر ردیف بر آن مقدم است.
    """
    source_type, source_id, source_number = source
    default_center = cost_center if cost_center is not None else cost_center_for_source(source_type)
    clean = []
    for line in lines:
        code, debit, credit, party, text = line[:5]
        center = line[5] if len(line) > 5 and line[5] else default_center
        net = int(debit or 0) - int(credit or 0)
        if net == 0:
            continue
        clean.append((code, max(net, 0), max(-net, 0), party, text, center))
    if not clean:
        return None
    total_debit = sum(line[1] for line in clean)
    total_credit = sum(line[2] for line in clean)
    if total_debit != total_credit:
        raise DocumentError(f"سند حسابداری تراز نیست (بدهکار {total_debit} ≠ بستانکار {total_credit}).")
    check_period_open(date)
    centers = _cost_centers(line[5] for line in clean)
    with transaction.atomic():
        entry = JournalEntry.objects.create(
            number=next_number("JV", date), date=date, description=description[:300], source_type=source_type,
            source_id=source_id, source_number=source_number, is_reversal=reverses is not None, reverses=reverses,
            created_by=user,
        )
        accounts = {a.code: a for a in Account.objects.filter(code__in={line[0] for line in clean})}
        missing = {line[0] for line in clean} - set(accounts)
        if missing:
            raise DocumentError(f"حساب تعریف نشده: {', '.join(sorted(missing))}")
        JournalLine.objects.bulk_create(
            JournalLine(entry=entry, account=accounts[code], party=party, debit=debit, credit=credit, description=text[:200],
                        cost_center=centers.get(center))
            for code, debit, credit, party, text, center in clean
        )
    return entry


def reverse_entry_for(doc, user, inventory_actual=None):
    """سند برگشتی: آرتیکل‌های سند اصلی معکوس می‌شوند.

    inventory_actual: ارزش واقعی حرکت کالای برگشتی (با علامت، از دید حساب موجودی). اگر با سند اصلی فرق کند
    (چون خروج با میانگین موزون روز انجام می‌شود)، اختلاف به حساب تعدیل بهای موجودی می‌رود تا حساب موجودی
    همیشه با کاردکس برابر بماند.
    """
    source = source_of(doc)
    original = JournalEntry.objects.filter(source_type=source[0], source_id=source[1], is_reversal=False).first()
    if original is None:
        return None
    lines = []
    inventory_original = 0
    for line in original.lines.select_related("account", "party", "cost_center"):
        if line.account.code == INVENTORY:
            inventory_original += line.debit - line.credit
            continue
        center = line.cost_center.code if line.cost_center_id else None
        lines.append((line.account.code, line.credit, line.debit, line.party, f"برگشت: {line.description}", center))
    inventory_reversal = -inventory_original if inventory_actual is None else inventory_actual
    if inventory_reversal:
        lines.append((INVENTORY, max(inventory_reversal, 0), max(-inventory_reversal, 0), None, "برگشت: موجودی کالا"))
    diff = -inventory_original - inventory_reversal
    if diff:
        lines.append((COST_ADJUSTMENT, max(diff, 0), max(-diff, 0), None, "اختلاف میانگین موزون در برگشت"))
    reason = getattr(doc, "reverse_reason", "")
    return post_entry(source, lines, f"برگشت {original.description} - {reason}", timezone.localdate(), user, reverses=original)


# --- دریافت و پرداخت و هزینه ---
def _party_account(party, direction):
    if party.kind == Party.Kind.SUPPLIER:
        return AP_SUPPLIERS
    if party.kind == Party.Kind.PROVIDER:
        return AP_PROVIDERS
    if party.kind == Party.Kind.EMPLOYEE:
        return SALARIES_PAYABLE
    if party.kind == Party.Kind.INSURER:
        return AR_INSURERS
    if party.kind == Party.Kind.CUSTOMER:
        return AR_CUSTOMERS
    return AP_OTHER


def validate_payment(payment):
    if payment.amount <= 0:
        raise DocumentError("مبلغ باید بیشتر از صفر باشد.")
    if payment.method in (PaymentMethod.CARD, PaymentMethod.BANK, PaymentMethod.CHEQUE) and not payment.reference_no.strip():
        raise DocumentError("برای پرداخت غیرنقدی شماره پیگیری یا شماره چک الزامی است.")


def post_payment(payment, user):
    money = METHOD_ACCOUNT[payment.method]
    counter = _party_account(payment.party, payment.direction)
    text = f"{payment.get_direction_display()} {payment.party} {payment.reference_no}".strip()
    if payment.direction == payment.Direction.PAY:
        lines = [(counter, payment.amount, 0, payment.party, text), (money, 0, payment.amount, None, text)]
    else:
        lines = [(money, payment.amount, 0, None, text), (counter, 0, payment.amount, payment.party, text)]
    post_entry(source_of(payment), lines, text, payment.date, user)


def validate_expense(expense):
    if expense.amount <= 0:
        raise DocumentError("مبلغ باید بیشتر از صفر باشد.")
    if expense.account.type != Account.Type.EXPENSE:
        raise DocumentError("حساب انتخاب‌شده از نوع هزینه نیست.")
    if not expense.method and not expense.party_id:
        raise DocumentError("برای هزینه نسیه، طرف حساب را مشخص کنید.")


def post_expense(expense, user):
    if expense.method:
        credit = (METHOD_ACCOUNT[expense.method], 0, expense.amount, None, expense.description)
    else:
        credit = (AP_OTHER if expense.party.kind != Party.Kind.SUPPLIER else AP_SUPPLIERS, 0, expense.amount, expense.party, expense.description)
    lines = [(expense.account.code, expense.amount, 0, expense.party, expense.description), credit]
    post_entry(source_of(expense), lines, f"هزینه: {expense.description}", expense.date, user)


# --- گزارش‌ها ---
def _lines(start=None, end=None):
    qs = JournalLine.objects.all()
    if start:
        qs = qs.filter(entry__date__gte=start)
    if end:
        qs = qs.filter(entry__date__lte=end)
    return qs


def trial_balance(start=None, end=None):
    rows = (
        _lines(start, end).values("account__code", "account__name", "account__type")
        .annotate(debit=Sum("debit"), credit=Sum("credit")).order_by("account__code")
    )
    result = []
    for row in rows:
        balance = row["debit"] - row["credit"]
        result.append({**row, "balance": balance})
    totals = {"debit": sum(r["debit"] for r in result), "credit": sum(r["credit"] for r in result)}
    return result, totals


def account_balance(code, start=None, end=None):
    agg = _lines(start, end).filter(account__code=code).aggregate(d=Sum("debit"), c=Sum("credit"))
    return (agg["d"] or 0) - (agg["c"] or 0)


def profit_and_loss(start=None, end=None):
    rows = (
        _lines(start, end).filter(account__type__in=[Account.Type.REVENUE, Account.Type.EXPENSE])
        .values("account__code", "account__name", "account__type").annotate(debit=Sum("debit"), credit=Sum("credit"))
        .order_by("account__code")
    )
    revenue, expenses = [], []
    for row in rows:
        if row["account__type"] == Account.Type.REVENUE:
            revenue.append({**row, "amount": row["credit"] - row["debit"]})
        else:
            expenses.append({**row, "amount": row["debit"] - row["credit"]})
    total_revenue = sum(r["amount"] for r in revenue)
    cogs = sum(r["amount"] for r in expenses if r["account__code"] == COGS)
    total_expenses = sum(r["amount"] for r in expenses)
    return {
        "revenue": revenue, "expenses": expenses, "total_revenue": total_revenue, "cogs": cogs,
        "gross_profit": total_revenue - cogs, "total_expenses": total_expenses, "net_profit": total_revenue - total_expenses,
    }


def party_balances(kind=None):
    """مانده هر طرف حساب: مثبت = طلب ما (بدهکار)، منفی = بدهی ما (بستانکار)."""
    qs = JournalLine.objects.filter(party__isnull=False)
    if kind:
        qs = qs.filter(party__kind=kind)
    rows = qs.values("party_id", "party__name", "party__kind").annotate(debit=Sum("debit"), credit=Sum("credit"))
    result = []
    for row in rows:
        balance = row["debit"] - row["credit"]
        if balance:
            result.append({**row, "balance": balance, "receivable": max(balance, 0), "payable": max(-balance, 0)})
    return sorted(result, key=lambda r: -abs(r["balance"]))


def party_statement(party):
    lines = (
        JournalLine.objects.filter(party=party).select_related("entry", "account")
        .exclude(Q(account__code=GRNI)).order_by("entry__date", "entry_id", "id")
    )
    running = 0
    result = []
    for line in lines:
        running += line.debit - line.credit
        result.append({"line": line, "running": running})
    return result


def totals_by_account_type():
    data = defaultdict(int)
    for row in JournalLine.objects.values("account__type").annotate(d=Sum("debit"), c=Sum("credit")):
        data[row["account__type"]] = row["d"] - row["c"]
    return data


# --- سند دستی ---
def validate_manual_journal(journal):
    lines = list(journal.lines.select_related("account"))
    if len(lines) < 2:
        raise DocumentError("سند دست‌کم دو ردیف لازم دارد.")
    for line in lines:
        if bool(line.debit) == bool(line.credit):
            raise DocumentError(f"ردیف {line.account}: فقط یکی از بدهکار یا بستانکار باید پر باشد.")
        if line.account.code in CONTROLLED_ACCOUNTS and journal.kind != journal.Kind.OPENING:
            raise DocumentError(f"حساب {line.account} زیردفتر عملیاتی دارد و سند دستی روی آن مجاز نیست.")
        if not line.account.is_active:
            raise DocumentError(f"حساب {line.account} غیرفعال است.")
    debit = sum(line.debit for line in lines)
    credit = sum(line.credit for line in lines)
    if debit != credit:
        raise DocumentError(f"سند تراز نیست (بدهکار {debit:,} ≠ بستانکار {credit:,}).")
    check_period_open(journal.date)


def post_manual_journal(journal, user):
    lines = [
        (line.account.code, line.debit, line.credit, line.party, line.description or journal.description,
         line.cost_center.code if line.cost_center_id else None)
        for line in journal.lines.select_related("account", "party", "cost_center")
    ]
    post_entry(source_of(journal), lines, journal.description, journal.date, user, cost_center="")


# --- دوره مالی ---
def close_period(period, user, note=""):
    """بستن دوره: سند ناتراز نباید وجود داشته باشد؛ در پایان سال، حساب‌های درآمد و هزینه به سود و زیان انباشته بسته می‌شوند."""
    from apps.audit import log
    from apps.audit.models import AuditLog

    with transaction.atomic():
        period = AccountingPeriod.objects.select_for_update().get(pk=period.pk)
        if period.status == AccountingPeriod.Status.CLOSED:
            raise DocumentError("این دوره قبلاً بسته شده است.")
        unbalanced = unbalanced_entries(period.start_date, period.end_date)
        if unbalanced:
            raise DocumentError(f"اسناد ناتراز در دوره وجود دارد: {', '.join(unbalanced[:5])}")
        closing = None
        if period.is_year_end:
            closing = _closing_entry(period, user)
        period.status = AccountingPeriod.Status.CLOSED
        period.closed_by, period.closed_at = user, timezone.now()
        period.note = (note or "")[:300]
        period.save(update_fields=["status", "closed_by", "closed_at", "note"])
        log.record(AuditLog.Action.STATUS, period, message=f"بستن دوره {period.name} {note}"[:500], user=user)
    return closing


def reopen_period(period, user, reason):
    from apps.audit import log
    from apps.audit.models import AuditLog

    if not (reason or "").strip():
        raise DocumentError("علت بازگشایی دوره را بنویسید.")
    with transaction.atomic():
        period = AccountingPeriod.objects.select_for_update().get(pk=period.pk)
        if period.status != AccountingPeriod.Status.CLOSED:
            raise DocumentError("این دوره باز است.")
        if period.is_year_end and JournalEntry.objects.filter(source_type="accounting.accountingperiod", source_id=period.pk,
                                                              is_reversal=False).exists():
            raise DocumentError("سند بستن سال صادر شده؛ ابتدا با سند دستی آن را اصلاح کنید.")
        period.status = AccountingPeriod.Status.OPEN
        period.note = reason.strip()[:300]
        period.save(update_fields=["status", "note"])
        log.record(AuditLog.Action.STATUS, period, message=f"بازگشایی دوره {period.name}: {reason}"[:500], user=user)
    return period


def _closing_entry(period, user):
    rows = (
        _lines(period.start_date, period.end_date)
        .filter(account__type__in=[Account.Type.REVENUE, Account.Type.EXPENSE])
        .values("account__code", "cost_center__code").annotate(d=Sum("debit"), c=Sum("credit"))
    )
    lines, net = [], 0
    for row in rows:
        balance = row["d"] - row["c"]
        if balance:
            lines.append((row["account__code"], max(-balance, 0), max(balance, 0), None, f"بستن حساب {period.name}",
                          row["cost_center__code"]))
            net += balance
    if not lines:
        return None
    lines.append((RETAINED_EARNINGS, max(net, 0), max(-net, 0), None, f"سود و زیان {period.name}", None))
    return post_entry(("accounting.accountingperiod", period.pk, period.name), lines, f"بستن حساب‌های موقت {period.name}",
                      period.end_date, user, cost_center="")


def unbalanced_entries(start=None, end=None):
    qs = JournalEntry.objects.all()
    if start:
        qs = qs.filter(date__gte=start)
    if end:
        qs = qs.filter(date__lte=end)
    rows = qs.annotate(d=Sum("lines__debit"), c=Sum("lines__credit")).values_list("number", "d", "c")
    return [number for number, d, c in rows if (d or 0) != (c or 0)]


# --- دفتر کل و گزارش‌های تفکیکی ---
def account_ledger(account_obj, start=None, end=None, cost_center=None):
    """دفتر حساب: مانده ابتدای دوره، گردش با مانده جاری و مانده پایان."""
    base = JournalLine.objects.filter(account=account_obj)
    if cost_center:
        base = base.filter(cost_center=cost_center)
    opening = 0
    if start:
        agg = base.filter(entry__date__lt=start).aggregate(d=Sum("debit"), c=Sum("credit"))
        opening = (agg["d"] or 0) - (agg["c"] or 0)
    rows = base.select_related("entry", "party", "cost_center")
    if start:
        rows = rows.filter(entry__date__gte=start)
    if end:
        rows = rows.filter(entry__date__lte=end)
    running, result = opening, []
    for line in rows.order_by("entry__date", "entry_id", "id"):
        running += line.debit - line.credit
        result.append({"line": line, "running": running})
    return {"opening": opening, "rows": result, "closing": running}


def journal_book(start=None, end=None):
    """دفتر روزنامه: اسناد به ترتیب تاریخ با آرتیکل‌ها."""
    qs = JournalEntry.objects.prefetch_related("lines__account", "lines__party", "lines__cost_center").order_by("date", "id")
    if start:
        qs = qs.filter(date__gte=start)
    if end:
        qs = qs.filter(date__lte=end)
    return qs


def profit_and_loss_by_center(start=None, end=None):
    rows = (
        _lines(start, end).filter(account__type__in=[Account.Type.REVENUE, Account.Type.EXPENSE])
        .values("cost_center__code", "cost_center__name", "account__type").annotate(d=Sum("debit"), c=Sum("credit"))
    )
    centers = {}
    for row in rows:
        key = row["cost_center__code"] or ""
        item = centers.setdefault(key, {"code": key, "name": row["cost_center__name"] or "بدون مرکز", "revenue": 0, "expense": 0})
        if row["account__type"] == Account.Type.REVENUE:
            item["revenue"] += row["c"] - row["d"]
        else:
            item["expense"] += row["d"] - row["c"]
    result = sorted(centers.values(), key=lambda r: r["code"] or "~")
    for item in result:
        item["profit"] = item["revenue"] - item["expense"]
    return result


def cash_flow(start=None, end=None):
    """گردش وجوه نقد و بانک به تفکیک حساب و نوع سند منبع (روش مستقیم ساده)."""
    lines = _lines(start, end).filter(account__is_cash=True)
    by_account = list(lines.values("account__code", "account__name").annotate(inflow=Sum("debit"), outflow=Sum("credit"))
                      .order_by("account__code"))
    by_source = list(lines.values("entry__source_type").annotate(inflow=Sum("debit"), outflow=Sum("credit"))
                     .order_by("entry__source_type"))
    for row in by_account + by_source:
        row["net"] = row["inflow"] - row["outflow"]
    return {"accounts": by_account, "sources": by_source,
            "inflow": sum(r["inflow"] for r in by_account), "outflow": sum(r["outflow"] for r in by_account)}


# --- مغایرت‌گیری بانک ---
def reconciliation_summary(rec):
    cleared_ids = set(rec.items.values_list("line_id", flat=True))
    lines = JournalLine.objects.filter(account=rec.account, entry__date__lte=rec.statement_date).select_related("entry")
    book = lines.aggregate(d=Sum("debit"), c=Sum("credit"))
    book_balance = (book["d"] or 0) - (book["c"] or 0)
    uncleared = [line for line in lines.exclude(reconciled__isnull=False).order_by("entry__date", "id")]
    cleared = lines.filter(pk__in=cleared_ids).aggregate(d=Sum("debit"), c=Sum("credit"))
    previously = lines.filter(reconciled__isnull=False).exclude(pk__in=cleared_ids).aggregate(d=Sum("debit"), c=Sum("credit"))
    cleared_balance = sum((x["d"] or 0) - (x["c"] or 0) for x in (cleared, previously))
    return {"book_balance": book_balance, "cleared_balance": cleared_balance, "uncleared": uncleared,
            "difference": rec.statement_balance - cleared_balance}


def mark_cleared(rec, line_ids, bank_reference=""):
    from .models import BankReconciliation, ReconciledLine

    if rec.status != BankReconciliation.Status.DRAFT:
        raise DocumentError("مغایرت‌گیری نهایی شده است.")
    lines = JournalLine.objects.filter(pk__in=line_ids, account=rec.account, entry__date__lte=rec.statement_date,
                                       reconciled__isnull=True)
    created = [ReconciledLine(reconciliation=rec, line=line, bank_reference=bank_reference[:60]) for line in lines]
    ReconciledLine.objects.bulk_create(created)
    return len(created)


def finalize_reconciliation(rec, user, note=""):
    from apps.audit import log
    from apps.audit.models import AuditLog

    from .models import BankReconciliation

    summary = reconciliation_summary(rec)
    if summary["difference"] and not (note or rec.note).strip():
        raise DocumentError(f"اختلاف {summary['difference']:,} ریال است؛ علت آن را در توضیح بنویسید.")
    rec.status, rec.finalized_by, rec.finalized_at = BankReconciliation.Status.FINAL, user, timezone.now()
    if note:
        rec.note = note[:300]
    rec.save(update_fields=["status", "finalized_by", "finalized_at", "note"])
    log.record(AuditLog.Action.STATUS, rec, message=f"نهایی شدن مغایرت‌گیری؛ اختلاف {summary['difference']:,}", user=user)
    return summary

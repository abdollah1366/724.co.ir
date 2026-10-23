"""ثبت اسناد حسابداری دوطرفه و گزارش‌های مالی."""

from collections import defaultdict

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.audit.documents import DocumentError, next_number

from .models import Account, JournalEntry, JournalLine, Party, PaymentMethod

# کد حساب‌های سیستمی (در مهاجرت 0002 ساخته می‌شوند)
CASH = "1101"
BANK = "1102"
AR_CUSTOMERS = "1103"
AR_INSURERS = "1104"
INVENTORY = "1105"
VAT_INPUT = "1106"
AP_SUPPLIERS = "2101"
GRNI = "2102"
VAT_OUTPUT = "2103"
AP_OTHER = "2104"
OPENING_EQUITY = "3102"
SALES = "4101"
SALES_RETURNS = "4102"
INVENTORY_GAIN = "4104"
COGS = "5101"
PURCHASE_VARIANCE = "5102"
INVENTORY_LOSS = "5103"
COST_ADJUSTMENT = "5104"
CASH_OVER_SHORT = "6105"

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


def post_entry(source, lines, description, date, user, reverses=None):
    """lines: فهرست (کد حساب، بدهکار، بستانکار، طرف حساب، شرح). سطرهای صفر حذف و تراز بررسی می‌شود."""
    source_type, source_id, source_number = source
    clean = []
    for code, debit, credit, party, text in lines:
        net = int(debit or 0) - int(credit or 0)
        if net == 0:
            continue
        clean.append((code, max(net, 0), max(-net, 0), party, text))
    if not clean:
        return None
    total_debit = sum(line[1] for line in clean)
    total_credit = sum(line[2] for line in clean)
    if total_debit != total_credit:
        raise DocumentError(f"سند حسابداری تراز نیست (بدهکار {total_debit} ≠ بستانکار {total_credit}).")
    with transaction.atomic():
        entry = JournalEntry.objects.create(
            number=next_number("JV", date), date=date, description=description[:300], source_type=source_type,
            source_id=source_id, source_number=source_number, is_reversal=reverses is not None, reverses=reverses,
            created_by=user,
        )
        accounts = {a.code: a for a in Account.objects.filter(code__in={line[0] for line in clean})}
        JournalLine.objects.bulk_create(
            JournalLine(entry=entry, account=accounts[code], party=party, debit=debit, credit=credit, description=text[:200])
            for code, debit, credit, party, text in clean
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
    for line in original.lines.select_related("account", "party"):
        if line.account.code == INVENTORY:
            inventory_original += line.debit - line.credit
            continue
        lines.append((line.account.code, line.credit, line.debit, line.party, f"برگشت: {line.description}"))
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

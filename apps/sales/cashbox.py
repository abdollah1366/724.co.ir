"""صندوق‌های همه بخش‌ها: شیفت، حساب نقد اختصاصی، صندوق‌دار مجاز و انتخاب حساب دریافت.

قواعد:
- هر صندوق حداکثر یک شیفت باز دارد و هر کاربر هم‌زمان فقط یک شیفت باز.
- اگر برای بخشی صندوق فعال تعریف شده باشد، دریافت نقد و کارت آن بخش فقط در شیفت باز صندوق همان بخش
  (یا صندوق مشترک) ممکن است و به حساب‌های همان صندوق می‌رود. تا وقتی صندوقی تعریف نشده، رفتار قبلی می‌ماند.
- نقد مورد انتظار صندوقی که حساب اختصاصی دارد = موجودی اولیه + گردش حساب نقد آن صندوق در طول شیفت.
"""

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.accounting import services as acc
from apps.accounting.models import Account, JournalLine
from apps.audit.documents import DocumentError

from .models import CashRegister, CashSession

Department = CashRegister.Department
CASH_PREFIX = acc.CASH  # حساب‌های صندوق‌ها: 1101 + دو رقم (110101، 110102، ...)


def create_cash_account(name):
    codes = Account.objects.filter(code__startswith=CASH_PREFIX).values_list("code", flat=True)
    used = [int(c[len(CASH_PREFIX):]) for c in codes if len(c) > len(CASH_PREFIX) and c[len(CASH_PREFIX):].isdigit()]
    code = f"{CASH_PREFIX}{(max(used) + 1 if used else 1):02d}"
    return Account.objects.create(code=code, name=f"صندوق {name}"[:150], type=Account.Type.ASSET, is_cash=True,
                                  bank_name=name[:80])


def serves(register, department):
    """آیا این صندوق دریافت‌های این بخش را می‌پذیرد؟ صندوق مشترک همه بخش‌ها جز داروخانه را می‌پذیرد."""
    if register.department == department:
        return True
    return register.department == Department.GENERAL and department not in (Department.PHARMACY,)


def registers_for(user, department=None):
    qs = CashRegister.objects.filter(is_active=True).select_related("warehouse").prefetch_related("cashiers")
    result = [r for r in qs if r.allows(user)]
    if department:
        result = [r for r in result if serves(r, department)]
    return result


def user_session(user, department=None):
    if not getattr(user, "is_authenticated", False):
        return None
    session = CashSession.objects.filter(cashier=user, status=CashSession.Status.OPEN).select_related(
        "register", "register__cash_account", "register__card_account").first()
    if session and department and not serves(session.register, department):
        return None
    return session


def department_has_registers(department):
    return any(serves(r, department) for r in CashRegister.objects.filter(is_active=True))


def require_session(user, department):
    """شیفت باز کاربر برای این بخش؛ اگر بخش صندوقی ندارد None (رفتار قبلی). در غیر این صورت خطا."""
    session = user_session(user, department)
    if session is None and department_has_registers(department):
        raise DocumentError(f"برای دریافت وجه {Department(department).label} ابتدا شیفت صندوق خود را باز کنید.")
    return session


def open_session(register, user, opening_cash=0):
    if not register.is_active:
        raise DocumentError("این صندوق غیرفعال است.")
    if not register.allows(user):
        raise DocumentError("شما صندوق‌دار مجاز این صندوق نیستید.")
    with transaction.atomic():
        CashRegister.objects.select_for_update().get(pk=register.pk)
        if CashSession.objects.filter(register=register, status=CashSession.Status.OPEN).exists():
            raise DocumentError("این صندوق یک شیفت باز دارد.")
        if CashSession.objects.filter(cashier=user, status=CashSession.Status.OPEN).exists():
            raise DocumentError("شما یک شیفت باز در صندوق دیگری دارید؛ ابتدا آن را ببندید.")
        return CashSession.objects.create(register=register, cashier=user, opening_cash=opening_cash)


def ledger_movement(session):
    """گردش حساب نقد اختصاصی صندوق در بازه شیفت (بدون سند کسری/اضافه خود شیفت‌ها)."""
    lines = JournalLine.objects.filter(account_id=session.register.cash_account_id,
                                       entry__created_at__gte=session.opened_at).exclude(entry__source_type="sales.cashsession")
    if session.closed_at:
        lines = lines.filter(entry__created_at__lte=session.closed_at)
    agg = lines.aggregate(d=Sum("debit"), c=Sum("credit"))
    return (agg["d"] or 0) - (agg["c"] or 0)


def money_account(session, method):
    """حساب دریافت برای روش پرداخت در این شیفت (نقد = حساب صندوق، کارت = حساب کارتخوان)."""
    if session is None:
        return None
    register = session.register
    if method == "cash":
        return register.cash_account
    if method == "card":
        return register.card_account
    return None


def money_code(session, method, default):
    account = money_account(session, method)
    return account.code if account else default


def status_rows():
    """برای صفحه مدیریت صندوق‌ها: هر صندوق با شیفت باز و مانده دفتر کل."""
    from .services import session_expected_cash

    rows = []
    open_sessions = {s.register_id: s for s in CashSession.objects.filter(status=CashSession.Status.OPEN)
                     .select_related("cashier")}
    for register in CashRegister.objects.select_related("warehouse", "cash_account", "card_account").prefetch_related("cashiers"):
        session = open_sessions.get(register.pk)
        rows.append({
            "register": register, "session": session,
            "expected": session_expected_cash(session) if session else None,
            "balance": acc.account_balance(register.cash_code),
            "cashiers": list(register.cashiers.all()),
            "last_closed": register.sessions.filter(status=CashSession.Status.CLOSED).order_by("-closed_at").first(),
        })
    return rows


def today_sessions():
    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    return CashSession.objects.filter(opened_at__gte=start).select_related("register", "cashier")

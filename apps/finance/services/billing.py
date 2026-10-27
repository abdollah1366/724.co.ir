"""صورتحساب، دریافت و استرداد: اعتبارسنجی، سند حسابداری و به‌روزرسانی مانده‌ها زیر قفل ردیف.

قواعد:
- مبلغ‌ها همیشه از سمت سرور (ردیف‌های صورتحساب) محاسبه می‌شوند؛ مبلغ ارسالی کاربر فقط برای تخصیص پرداخت است و
  بیشتر از مانده پذیرفته نمی‌شود.
- مانده بیمار در حساب 1108 و علی‌الحساب در 2105 است؛ هر دو زیردفتر از روی همین جدول‌ها قابل بازسازی و مغایرت‌گیری‌اند.
- ابطال صورتحساب پرداخت‌شده مجاز است: مبلغ پرداختی به بستانکاری بیمار تبدیل می‌شود و با «استرداد» پرداخت می‌شود.
"""

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q, Sum
from django.utils import timezone

from apps.accounting import services as acc
from apps.accounting.models import Account, CostCenter, Party
from apps.audit import log
from apps.audit.documents import DocStatus, DocumentError
from apps.audit.models import AuditLog

from ..models import Invoice, InvoiceLine, PaymentAllocation, Receipt, ReceiptTender, Refund, TenderMethod

DISCOUNT_ACCOUNT = "4209"
METHOD_ACCOUNT = {
    TenderMethod.CASH: acc.CASH, TenderMethod.CARD: acc.BANK, TenderMethod.BANK: acc.BANK,
    TenderMethod.CHEQUE: acc.BANK, TenderMethod.ONLINE: acc.BANK,
}
# (مرکز درآمد، حساب درآمد) پیش‌فرض هر بخش
MODULE_DEFAULTS = {
    Invoice.Module.CLINIC: ("CLINIC", "4201"),
    Invoice.Module.REHAB: ("REHAB", "4203"),
    Invoice.Module.TELEMED: ("TELE", "4202"),
    Invoice.Module.OTHER: ("CLINIC", "4201"),
}


def money_account_code(method, money_account=None):
    if money_account is not None:
        return money_account.code
    return METHOD_ACCOUNT[method]


def module_defaults(module):
    center, revenue = MODULE_DEFAULTS[module]
    return CostCenter.objects.get(code=center), Account.objects.get(code=revenue)


def _patient_filter(patient_id=None, patient_user_id=None, prefix=""):
    if patient_id:
        return Q(**{f"{prefix}patient_id": patient_id})
    if patient_user_id:
        return Q(**{f"{prefix}patient_id__isnull": True, f"{prefix}patient_user_id": patient_user_id})
    raise DocumentError("بیمار مشخص نیست.")


def same_patient(a, b):
    return a.patient_key == b.patient_key and a.patient_key[1] is not None


# ---------------------------------------------------------------- صورتحساب
def _check_lines(lines):
    for line in lines:
        if line.quantity <= 0:
            raise DocumentError(f"تعداد «{line.description}» نامعتبر است.")
        if line.discount > line.gross:
            raise DocumentError(f"تخفیف «{line.description}» بیشتر از مبلغ آن است.")
        if line.insurer_share > line.net:
            raise DocumentError(f"سهم بیمه «{line.description}» بیشتر از مبلغ پس از تخفیف است.")


def recalculate(invoice):
    lines = list(invoice.lines.all())
    _check_lines(lines)
    totals = {
        "gross": sum(line.gross for line in lines),
        "discount": sum(line.discount for line in lines),
        "insurer_share": sum(line.insurer_share for line in lines),
    }
    totals["patient_share"] = totals["gross"] - totals["discount"] - totals["insurer_share"]
    for key, value in totals.items():
        setattr(invoice, key, value)
    if invoice.pk:
        Invoice.objects.filter(pk=invoice.pk, status=DocStatus.DRAFT).update(**totals)
    return totals


def validate_invoice(invoice):
    lines = list(invoice.lines.all())
    if not lines:
        raise DocumentError("صورتحساب ردیف ندارد.")
    if not invoice.patient_id and not invoice.patient_user_id:
        raise DocumentError("بیمار صورتحساب مشخص نیست.")
    totals = recalculate(invoice)
    if totals["discount"] and not invoice.discount_reason.strip():
        raise DocumentError("علت تخفیف را وارد کنید.")
    if totals["insurer_share"] and not invoice.insurer_id:
        raise DocumentError("برای سهم بیمه، بیمه یا سازمان طرف قرارداد را انتخاب کنید.")
    if invoice.revenue_account.type != Account.Type.REVENUE:
        raise DocumentError("حساب درآمد صورتحساب از نوع درآمد نیست.")
    if not invoice.cost_center.is_active:
        raise DocumentError("مرکز درآمد غیرفعال است.")
    if invoice.source_id and Invoice.objects.filter(source_type=invoice.source_type, source_id=invoice.source_id).exclude(
            pk=invoice.pk).exclude(status=DocStatus.REVERSED).exists():
        raise DocumentError("برای این خدمت قبلاً صورتحساب فعال صادر شده است.")


def _insurer_account(insurer):
    return acc.AR_CUSTOMERS if insurer.kind == Party.Kind.CUSTOMER else acc.AR_INSURERS


def post_invoice(invoice, user):
    text = f"صورتحساب {invoice.number} {invoice.get_module_display()} - {invoice.patient_name}"
    lines = [
        (acc.AR_PATIENTS, invoice.patient_share, 0, None, f"سهم بیمار {invoice.number}"),
        (DISCOUNT_ACCOUNT, invoice.discount, 0, None, f"تخفیف: {invoice.discount_reason}"),
        (invoice.revenue_account.code, 0, invoice.gross, None, text),
    ]
    if invoice.insurer_share:
        lines.insert(1, (_insurer_account(invoice.insurer), invoice.insurer_share, 0, invoice.insurer, f"سهم بیمه {invoice.number}"))
    acc.post_entry(acc.source_of(invoice), lines, text, invoice.date, user, cost_center=invoice.cost_center.code)


def validate_invoice_reverse(invoice):
    pending = Refund.objects.filter(invoice=invoice, status=DocStatus.DRAFT, approved_at__isnull=False)
    if pending.exists():
        raise DocumentError("استرداد تأییدشده‌ای برای این صورتحساب در انتظار پرداخت است.")


def reverse_invoice(invoice, user):
    acc.reverse_entry_for(invoice, user)


@transaction.atomic
def create_invoice(*, patient_name, lines, user, module=Invoice.Module.CLINIC, patient_id=None, patient_user_id=None,
                   provider_id=None, provider_name="", source=None, source_ref="", insurer=None, insurance_ref="",
                   discount_reason="", event=None, date=None, post=True, note=""):
    """ساخت صورتحساب. lines: فهرست dict با description، unit_price و اختیاری quantity، service_code، discount،
    insurer_share، provider_id، provider_name، source_line_ref. source: (نوع منبع، شناسه)."""
    center, revenue = module_defaults(module)
    source_type, source_id = source or ("", "")
    invoice = Invoice.objects.create(
        module=module, patient_id=patient_id, patient_user_id=patient_user_id, patient_name=patient_name[:150],
        provider_id=provider_id, provider_name=provider_name[:150], source_type=source_type, source_id=str(source_id),
        source_ref=source_ref[:60], cost_center=center, revenue_account=revenue, insurer=insurer,
        insurance_ref=insurance_ref[:60], discount_reason=discount_reason[:200], event=event, created_by=user,
        date=date or timezone.localdate(), note=note[:500],
    )
    for item in lines:
        InvoiceLine.objects.create(
            invoice=invoice, description=item["description"][:200], unit_price=int(item["unit_price"]),
            quantity=int(item.get("quantity", 1)), service_code=item.get("service_code", "")[:20],
            discount=int(item.get("discount", 0)), insurer_share=int(item.get("insurer_share", 0)),
            provider_id=item.get("provider_id", provider_id), provider_name=item.get("provider_name", provider_name)[:150],
            source_line_ref=str(item.get("source_line_ref", ""))[:64],
        )
    recalculate(invoice)
    if post:
        invoice.post(user)
    return invoice


# ---------------------------------------------------------------- علی‌الحساب
def advance_balance(patient_id=None, patient_user_id=None, exclude_receipt=None):
    """علی‌الحساب بیمار = مازاد دریافت‌ها − برداشت از علی‌الحساب − استرداد از علی‌الحساب (فقط اسناد ثبت‌شده)."""
    who = _patient_filter(patient_id, patient_user_id)
    receipts = Receipt.objects.filter(who, status=DocStatus.POSTED)
    if exclude_receipt is not None:
        receipts = receipts.exclude(pk=exclude_receipt.pk)
    tendered = ReceiptTender.objects.filter(receipt__in=receipts).aggregate(s=Sum("amount"))["s"] or 0
    used = ReceiptTender.objects.filter(receipt__in=receipts, method=TenderMethod.ADVANCE).aggregate(s=Sum("amount"))["s"] or 0
    allocated = PaymentAllocation.objects.filter(receipt__in=receipts).aggregate(s=Sum("amount"))["s"] or 0
    refunded = Refund.objects.filter(who, status=DocStatus.POSTED, invoice__isnull=True).aggregate(s=Sum("amount"))["s"] or 0
    return tendered - allocated - used - refunded


def open_invoices(patient_id=None, patient_user_id=None):
    return (
        Invoice.objects.filter(_patient_filter(patient_id, patient_user_id), status=DocStatus.POSTED)
        .filter(paid_amount__lt=F("patient_share")).order_by("date", "id")
    )


def refundable_invoices(patient_id=None, patient_user_id=None):
    return Invoice.objects.filter(_patient_filter(patient_id, patient_user_id), status=DocStatus.REVERSED, paid_amount__gt=0)


# ---------------------------------------------------------------- دریافت
def _require_reference(method, reference, what="پرداخت"):
    if method not in (TenderMethod.CASH, TenderMethod.ADVANCE) and not (reference or "").strip():
        raise DocumentError(f"برای {what} غیرنقدی شماره پیگیری یا شماره چک الزامی است.")


def validate_receipt(receipt):
    tenders = list(receipt.tenders.select_related("money_account"))
    if not tenders:
        raise DocumentError("مبلغ و روش پرداخت را وارد کنید.")
    for tender in tenders:
        _require_reference(tender.method, tender.reference_no)
        if tender.money_account_id and not tender.money_account.is_cash:
            raise DocumentError("حساب انتخاب‌شده صندوق یا بانک نیست.")
    total = sum(t.amount for t in tenders)
    from_advance = sum(t.amount for t in tenders if t.method == TenderMethod.ADVANCE)
    allocations = list(receipt.allocations.order_by("invoice_id"))
    allocated = sum(a.amount for a in allocations)
    if allocated > total:
        raise DocumentError("جمع تخصیص به صورتحساب‌ها بیشتر از مبلغ دریافتی است.")
    if from_advance:
        if allocated < from_advance:
            raise DocumentError("برداشت از علی‌الحساب فقط برای تسویه صورتحساب است.")
        available = advance_balance(receipt.patient_id, receipt.patient_user_id)
        if from_advance > available:
            raise DocumentError(f"علی‌الحساب بیمار {available:,} ریال است.")
    invoice_ids = [a.invoice_id for a in allocations]
    locked = {i.pk: i for i in Invoice.objects.select_for_update().filter(pk__in=invoice_ids).order_by("pk")}
    for allocation in allocations:
        invoice = locked[allocation.invoice_id]
        if invoice.status != DocStatus.POSTED:
            raise DocumentError(f"صورتحساب {invoice} ثبت نهایی نشده یا ابطال شده است.")
        if not same_patient(invoice, receipt):
            raise DocumentError(f"صورتحساب {invoice.number} متعلق به این بیمار نیست.")
        if allocation.amount > invoice.balance_due:
            raise DocumentError(f"مبلغ تخصیص به {invoice.number} بیشتر از مانده آن ({invoice.balance_due:,} ریال) است.")


def post_receipt(receipt, user):
    tenders = list(receipt.tenders.select_related("money_account"))
    allocations = list(receipt.allocations.select_related("invoice"))
    total = sum(t.amount for t in tenders)
    allocated = sum(a.amount for a in allocations)
    text = f"دریافت {receipt.number} از {receipt.patient_name}"
    lines = []
    for tender in tenders:
        code = acc.PATIENT_ADVANCES if tender.method == TenderMethod.ADVANCE else money_account_code(tender.method, tender.money_account)
        lines.append((code, tender.amount, 0, None, f"{tender.get_method_display()} {tender.reference_no}".strip()))
    if allocated:
        numbers = "، ".join(a.invoice.number for a in allocations)
        lines.append((acc.AR_PATIENTS, 0, allocated, None, f"بابت {numbers}"[:200]))
    if total > allocated:
        lines.append((acc.PATIENT_ADVANCES, 0, total - allocated, None, "علی‌الحساب بیمار"))
    acc.post_entry(acc.source_of(receipt), lines, text, receipt.date, user, cost_center="")
    for allocation in allocations:
        Invoice.objects.filter(pk=allocation.invoice_id).update(paid_amount=F("paid_amount") + allocation.amount)


def validate_receipt_reverse(receipt):
    remainder = receipt.total - receipt.allocated
    if remainder > 0:
        available = advance_balance(receipt.patient_id, receipt.patient_user_id)
        if available < remainder:
            raise DocumentError("علی‌الحساب این دریافت مصرف یا مسترد شده است؛ ابتدا آن اسناد را برگشت بزنید.")
    ids = list(receipt.allocations.values_list("invoice_id", flat=True))
    for invoice in Invoice.objects.select_for_update().filter(pk__in=ids).order_by("pk"):
        amount = receipt.allocations.get(invoice=invoice).amount
        if invoice.paid_amount < amount:
            raise DocumentError(f"از مبلغ این دریافت برای {invoice.number} استرداد شده است؛ ابتدا استرداد را برگشت بزنید.")


def reverse_receipt(receipt, user):
    for allocation in receipt.allocations.all():
        Invoice.objects.filter(pk=allocation.invoice_id).update(paid_amount=F("paid_amount") - allocation.amount)
    acc.reverse_entry_for(receipt, user)


def allocate_oldest_first(invoices, amount):
    """تقسیم مبلغ روی صورتحساب‌ها از قدیمی‌ترین؛ خروجی: [(صورتحساب، مبلغ)] و باقی‌مانده."""
    result = []
    for invoice in sorted(invoices, key=lambda i: (i.date, i.pk)):
        if amount <= 0:
            break
        share = min(amount, invoice.balance_due)
        if share > 0:
            result.append((invoice, share))
            amount -= share
    return result, amount


def receive(*, patient_name, tenders, user, patient_id=None, patient_user_id=None, invoices=(), allocations=None,
            idempotency_key=None, date=None, source_ref="", note=""):
    """ثبت و نهایی کردن دریافت. tenders: [{"method", "amount", "reference_no", "money_account"}].

    allocations: [(صورتحساب، مبلغ)]؛ اگر خالی باشد، مبلغ به ترتیب قدیمی‌ترین روی invoices تخصیص و مازاد علی‌الحساب می‌شود.
    idempotency_key: ارسال دوباره همان درخواست (دوبار کلیک، تکرار شبکه) همان دریافت قبلی را برمی‌گرداند.
    """
    if idempotency_key:
        existing = Receipt.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return existing
    total = sum(int(t["amount"]) for t in tenders)
    if allocations is None:
        allocations, _ = allocate_oldest_first(list(invoices), total)
    with transaction.atomic():
        receipt = Receipt.objects.create(
            patient_id=patient_id, patient_user_id=patient_user_id, patient_name=patient_name[:150], created_by=user,
            idempotency_key=idempotency_key, date=date or timezone.localdate(), source_ref=source_ref[:60], note=note[:500],
        )
        for t in tenders:
            if int(t["amount"]) <= 0:
                continue
            ReceiptTender.objects.create(receipt=receipt, method=t["method"], amount=int(t["amount"]),
                                         reference_no=(t.get("reference_no") or "")[:64], money_account=t.get("money_account"))
        for invoice, amount in allocations:
            if amount > 0:
                PaymentAllocation.objects.create(receipt=receipt, invoice=invoice, amount=int(amount))
        receipt.post(user)
    return receipt


# ---------------------------------------------------------------- استرداد
def needs_approval(refund):
    return refund.amount >= settings.FINANCE_RULES["refund_approval_threshold"]


def approve_refund(refund, user, note=""):
    if not user.has_perm("finance.approve_refund"):
        raise DocumentError("مجوز تأیید استرداد ندارید.")
    with transaction.atomic():
        refund = Refund.objects.select_for_update().get(pk=refund.pk)
        if refund.status != DocStatus.DRAFT:
            raise DocumentError("فقط استرداد پیش‌نویس تأیید می‌شود.")
        if refund.approved_at:
            raise DocumentError("این استرداد قبلاً تأیید شده است.")
        if settings.FINANCE_RULES["four_eyes"] and refund.created_by_id == user.pk:
            raise DocumentError("درخواست‌دهنده استرداد نمی‌تواند خودش آن را تأیید کند.")
        Refund.objects.filter(pk=refund.pk).update(approved_by=user, approved_at=timezone.now())
        refund.refresh_from_db()
        log.record(AuditLog.Action.STATUS, refund, message=f"تأیید استرداد {refund.amount:,} ریال {note}"[:500], user=user)
    return refund


def validate_refund(refund):
    if not refund.reason.strip():
        raise DocumentError("علت استرداد را بنویسید.")
    _require_reference(refund.method, refund.reference_no, "استرداد")
    if needs_approval(refund) and not refund.approved_at:
        raise DocumentError("این استرداد هنوز تأیید نشده است.")
    if refund.invoice_id:
        invoice = Invoice.objects.select_for_update().get(pk=refund.invoice_id)
        if invoice.status == DocStatus.DRAFT:
            raise DocumentError("صورتحساب ثبت نهایی نشده است.")
        if not same_patient(invoice, refund):
            raise DocumentError("صورتحساب متعلق به این بیمار نیست.")
        if refund.amount > invoice.paid_amount:
            raise DocumentError(f"مبلغ استرداد بیشتر از مبلغ پرداختی صورتحساب ({invoice.paid_amount:,} ریال) است.")
    else:
        available = advance_balance(refund.patient_id, refund.patient_user_id)
        if refund.amount > available:
            raise DocumentError(f"علی‌الحساب بیمار {available:,} ریال است.")


def post_refund(refund, user):
    counter = acc.AR_PATIENTS if refund.invoice_id else acc.PATIENT_ADVANCES
    money = money_account_code(refund.method, refund.money_account)
    text = f"استرداد {refund.number} به {refund.patient_name}: {refund.reason}"
    acc.post_entry(acc.source_of(refund), [(counter, refund.amount, 0, None, text), (money, 0, refund.amount, None, text)],
                   text, refund.date, user, cost_center="")
    if refund.invoice_id:
        Invoice.objects.filter(pk=refund.invoice_id).update(paid_amount=F("paid_amount") - refund.amount)


def reverse_refund(refund, user):
    if refund.invoice_id:
        invoice = Invoice.objects.select_for_update().get(pk=refund.invoice_id)
        if invoice.status == DocStatus.POSTED and invoice.paid_amount + refund.amount > invoice.patient_share:
            raise DocumentError("برگشت این استرداد مبلغ پرداختی صورتحساب را بیشتر از سهم بیمار می‌کند.")
        Invoice.objects.filter(pk=refund.invoice_id).update(paid_amount=F("paid_amount") + refund.amount)
    acc.reverse_entry_for(refund, user)


def request_refund(*, patient_name, amount, method, reason, user, invoice=None, patient_id=None, patient_user_id=None,
                   reference_no="", money_account=None, idempotency_key=None):
    if idempotency_key:
        existing = Refund.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return existing
    if invoice is not None:
        patient_id, patient_user_id, patient_name = invoice.patient_id, invoice.patient_user_id, invoice.patient_name
    return Refund.objects.create(
        invoice=invoice, patient_id=patient_id, patient_user_id=patient_user_id, patient_name=patient_name[:150],
        amount=int(amount), method=method, reason=reason[:300], reference_no=reference_no[:64], money_account=money_account,
        created_by=user, idempotency_key=idempotency_key,
    )


def patient_summary(patient_id=None, patient_user_id=None):
    who = _patient_filter(patient_id, patient_user_id)
    invoices = Invoice.objects.filter(who).exclude(status=DocStatus.DRAFT)
    due = sum(i.balance_due for i in invoices.filter(status=DocStatus.POSTED))
    credit = sum(i.paid_amount for i in invoices.filter(status=DocStatus.REVERSED))
    return {"due": due, "credit": credit, "advance": advance_balance(patient_id, patient_user_id)}


def receivables_aging(today=None):
    """سنی مطالبات بیماران (صورتحساب‌های ثبت‌شده با مانده) در بازه‌های ۰-۳۰، ۳۱-۶۰، ۶۱-۹۰ و بیش از ۹۰ روز."""
    today = today or timezone.localdate()
    buckets = {"0-30": 0, "31-60": 0, "61-90": 0, "90+": 0}
    rows = []
    for invoice in Invoice.objects.filter(status=DocStatus.POSTED, paid_amount__lt=F("patient_share")).order_by("date"):
        age = (today - invoice.date).days
        key = "0-30" if age <= 30 else "31-60" if age <= 60 else "61-90" if age <= 90 else "90+"
        buckets[key] += invoice.balance_due
        rows.append({"invoice": invoice, "age": age, "bucket": key})
    return {"buckets": buckets, "rows": rows, "total": sum(buckets.values())}


def subledger_totals():
    """زیردفتر بیماران برای مغایرت‌گیری با دفتر کل: مانده 1108 و 2105 از روی صورتحساب‌ها و دریافت‌ها."""
    posted = Invoice.objects.filter(status=DocStatus.POSTED).aggregate(s=Sum("patient_share"), p=Sum("paid_amount"))
    reversed_paid = Invoice.objects.filter(status=DocStatus.REVERSED).aggregate(p=Sum("paid_amount"))["p"] or 0
    ar = (posted["s"] or 0) - (posted["p"] or 0) - reversed_paid
    receipts = Receipt.objects.filter(status=DocStatus.POSTED)
    tendered = ReceiptTender.objects.filter(receipt__in=receipts).aggregate(s=Sum("amount"))["s"] or 0
    used = ReceiptTender.objects.filter(receipt__in=receipts, method=TenderMethod.ADVANCE).aggregate(s=Sum("amount"))["s"] or 0
    allocated = PaymentAllocation.objects.filter(receipt__in=receipts).aggregate(s=Sum("amount"))["s"] or 0
    refunded = Refund.objects.filter(status=DocStatus.POSTED, invoice__isnull=True).aggregate(s=Sum("amount"))["s"] or 0
    return {"ar_patients": ar, "advances": tendered - allocated - used - refunded}

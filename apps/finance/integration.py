"""مصرف‌کننده‌های مالی رویدادهای ماژول‌های دیگر (adapterها). هر handler idempotent است.

| رویداد | قابلیت | نتیجه |
|---|---|---|
| ehr.encounter.signed | finance.integration.clinic | صورتحساب ویزیت (پیش‌نویس یا نهایی طبق FINANCE_RULES) |
| ehr.therapysession.signed | finance.integration.rehab | صورتحساب جلسه توانبخشی |
| telemed.payment.paid | finance.integration.telemed | صورتحساب نهایی + دریافت (سند درآمد و بانک) |
| telemed.payment.refund_due | finance.integration.telemed | ابطال صورتحساب ← بستانکاری بیمار |
| telemed.payment.refunded | finance.integration.telemed | سند استرداد (بانک) |
| telemed.payment.failed | finance.integration.telemed | فقط ثبت برای مغایرت‌گیری؛ بدون اثر مالی |
| <سند داروخانه/آزمایشگاه>.posted/reversed | finance.integration.pharmacy / lab | پیوند رویداد به سند حسابداری همان سند |

ثبت حسابداری داروخانه و آزمایشگاه عمداً همان ثبت هم‌زمان داخل تراکنش خودشان می‌ماند (کاردکس = دفتر کل)؛ اینجا فقط رهگیری
و کنترل وجود سند انجام می‌شود تا ثبت دوباره رخ ندهد.
"""

import datetime

from django.conf import settings
from django.utils import timezone

from apps.accounting.models import JournalEntry
from apps.audit.documents import DocStatus
from apps.core.events import subscribe

from .models import Invoice, Receipt, Refund, TenderMethod
from .services import billing


def _active_invoice(source_type, source_id):
    return Invoice.objects.filter(source_type=source_type, source_id=str(source_id)).exclude(status=DocStatus.REVERSED).first()


def _label(invoice):
    return invoice.number or f"پیش‌نویس صورتحساب #{invoice.pk}"


def _invoice_for_service(event, *, module, source_type, source_id, lines, unpriced, auto_post, source_ref=""):
    p = event.payload
    note = ""
    if unpriced:
        note = "تعرفه این خدمات ثبت نشده و باید پیش از ثبت نهایی وارد شود: " + "، ".join(unpriced)
    invoice = billing.create_invoice(
        module=module, patient_id=p.get("patient_id"), patient_name=p.get("patient_name") or "بیمار",
        provider_id=p.get("provider_id"), provider_name=p.get("provider_name", ""), source=(source_type, source_id),
        source_ref=source_ref, lines=lines, user=None, event=event, date=_date(p.get("date")),
        post=auto_post and not unpriced, note=note,
    )
    return _label(invoice)


def _date(value):
    try:
        return datetime.date.fromisoformat(value) if value else timezone.localdate()
    except ValueError:
        return timezone.localdate()


@subscribe("ehr.encounter.signed", flag="finance.integration.clinic")
def on_encounter_signed(event):
    p = event.payload
    existing = _active_invoice("ehr.encounter", p["encounter_id"])
    if existing:
        return _label(existing)
    if p.get("appointment_id"):
        prepaid = _active_invoice("appointments.appointment", p["appointment_id"])
        if prepaid:
            return f"قبلاً در پذیرش صورتحساب شده: {_label(prepaid)}"
    lines, unpriced = [], []
    for s in p.get("services", []):
        lines.append({"description": s["title"] or s["code"], "service_code": s["code"], "quantity": s.get("quantity") or 1,
                      "unit_price": s.get("unit_tariff") or 0, "source_line_ref": s.get("line_ref", "")})
        if s.get("unit_tariff") is None:
            unpriced.append(s["code"])
    if not lines and settings.FINANCE_RULES["bill_visit_fee_without_services"] and p.get("visit_fee"):
        lines = [{"description": "ویزیت", "service_code": "VISIT", "unit_price": p["visit_fee"]}]
    if not lines:
        return "بدون خدمت قابل صورتحساب"
    return _invoice_for_service(event, module=Invoice.Module.CLINIC, source_type="ehr.encounter", source_id=p["encounter_id"],
                                lines=lines, unpriced=unpriced, auto_post=settings.FINANCE_RULES["auto_post_encounter_invoices"])


@subscribe("ehr.therapysession.signed", flag="finance.integration.rehab")
def on_session_signed(event):
    p = event.payload
    existing = _active_invoice("ehr.therapysession", p["session_id"])
    if existing:
        return _label(existing)
    if p.get("attendance") != "attended" or not p.get("code"):
        return "جلسه بدون خدمت قابل صورتحساب (غیبت، لغو یا بدون کد خدمت)"
    unpriced = [p["code"]] if p.get("unit_tariff") is None else []
    lines = [{"description": p["title"] or p["code"], "service_code": p["code"], "unit_price": p.get("unit_tariff") or 0,
              "source_line_ref": p["session_id"]}]
    return _invoice_for_service(event, module=Invoice.Module.REHAB, source_type="ehr.therapysession", source_id=p["session_id"],
                                lines=lines, unpriced=unpriced, auto_post=settings.FINANCE_RULES["auto_post_session_invoices"])


# ---------------------------------------------------------------- مشاوره آنلاین
def _telemed_reference(p, field="reference"):
    # اگر شماره پیگیری بانکی ثبت نشده باشد، مرجع داخلی قابل رهگیری به پرداخت مشاوره ثبت می‌شود.
    return p.get(field) or f"TELEMED-{p['code']}-{p['payment_id']}"


@subscribe("telemed.payment.paid", flag="finance.integration.telemed")
def on_consult_paid(event):
    p = event.payload
    invoice = _active_invoice("telemed.consultation", p["consultation_id"])
    if invoice is None:
        invoice = billing.create_invoice(
            module=Invoice.Module.TELEMED, patient_user_id=p["patient_user_id"], patient_name=p["patient_name"],
            provider_id=p.get("doctor_id"), provider_name=p.get("doctor_name", ""), source=("telemed.consultation", p["consultation_id"]),
            source_ref=p["code"], user=None, event=event, date=_date(p.get("at", "")[:10]),
            lines=[{"description": f"{p['kind_label']} ({p['code']})", "service_code": f"TELE-{p['kind']}", "unit_price": p["amount"]}],
        )
    method = TenderMethod.ONLINE if p.get("method") == "online" else TenderMethod.CARD
    allocate = min(p["amount"], invoice.balance_due) if invoice.status == DocStatus.POSTED else 0
    receipt = billing.receive(
        patient_user_id=p["patient_user_id"], patient_name=p["patient_name"], user=None,
        tenders=[{"method": method, "amount": p["amount"], "reference_no": _telemed_reference(p)}],
        allocations=[(invoice, allocate)] if allocate else [], idempotency_key=f"telemed-payment-{p['payment_id']}",
        source_ref=p["code"], date=_date(p.get("at", "")[:10]),
    )
    return f"{_label(invoice)} / {receipt.number}"


def _payment_receipt(p):
    receipt = Receipt.objects.filter(idempotency_key=f"telemed-payment-{p['payment_id']}", status=DocStatus.POSTED).first()
    if receipt is None:
        # رویداد پرداخت هنوز پردازش نشده (مثلاً خطا داده)؛ با تلاش مجدد بعداً ادامه می‌یابد.
        raise LookupError(f"دریافت پرداخت مشاوره {p['code']} هنوز ثبت نشده است.")
    return receipt


@subscribe("telemed.payment.refund_due", flag="finance.integration.telemed")
def on_consult_refund_due(event):
    p = event.payload
    receipt = _payment_receipt(p)
    allocation = receipt.allocations.select_related("invoice").first()
    if allocation is None:
        return f"مبلغ در علی‌الحساب بیمار است ({receipt.number})"
    invoice = allocation.invoice
    if invoice.status == DocStatus.REVERSED:
        return f"{invoice.number} قبلاً ابطال شده"
    invoice.reverse(None, f"مشاوره {p['code']} انجام نشد؛ مبلغ قابل استرداد است")
    return f"ابطال {invoice.number}"


@subscribe("telemed.payment.refunded", flag="finance.integration.telemed")
def on_consult_refunded(event):
    p = event.payload
    receipt = _payment_receipt(p)
    allocation = receipt.allocations.select_related("invoice").first()
    reference = _telemed_reference(p, "refund_reference")
    parts = []
    invoice_amount = allocation.amount if allocation else 0
    if invoice_amount:
        invoice = allocation.invoice
        if invoice.status != DocStatus.REVERSED:
            raise LookupError(f"صورتحساب {invoice.number} هنوز ابطال نشده؛ رویداد قابل استرداد پردازش نشده است.")
        parts.append((invoice, invoice_amount, "inv"))
    if receipt.total > invoice_amount:
        parts.append((None, receipt.total - invoice_amount, "adv"))
    numbers = []
    for invoice, amount, suffix in parts:
        refund = billing.request_refund(
            invoice=invoice, patient_user_id=p["patient_user_id"], patient_name=p["patient_name"], amount=amount,
            method=TenderMethod.BANK, reference_no=reference, user=None,
            reason=f"استرداد مشاوره {p['code']} (تأییدشده در ماژول مشاوره آنلاین)",
            idempotency_key=f"telemed-refund-{p['payment_id']}-{suffix}",
        )
        if refund.status == DocStatus.DRAFT:
            # تصمیم استرداد را کاربر دارای مجوز telemed.manage_payments در ماژول مالک گرفته است.
            Refund.objects.filter(pk=refund.pk).update(approved_at=timezone.now())
            refund.refresh_from_db()
            refund.post(None)
        numbers.append(refund.number)
    return "، ".join(numbers) or "بدون مبلغ"


@subscribe("telemed.payment.failed", flag="finance.integration.telemed")
def on_consult_payment_failed(event):
    return "پرداخت ناموفق؛ بدون اثر مالی"


# ---------------------------------------------------------------- داروخانه و آزمایشگاه
PHARMACY_DOCUMENTS = ("sales.salesinvoice", "sales.salesreturn", "purchasing.goodsreceipt", "purchasing.purchaseinvoice",
                      "inventory.inventorydocument")
LAB_DOCUMENTS = ("lab.laborder", "lab.labpayment", "lab.claimsettlement")


def link_ledger(event):
    """سند حسابداری همان سند منبع را پیدا و به رویداد پیوند می‌دهد؛ هیچ سند تازه‌ای نمی‌سازد."""
    reversal = event.event_type.endswith(".reversed")
    entry = JournalEntry.objects.filter(source_type=event.source_type, source_id=int(event.source_id), is_reversal=reversal).first()
    return entry.number if entry else "بدون اثر مالی"


for _label_prefix, _flag in [(d, "finance.integration.pharmacy") for d in PHARMACY_DOCUMENTS] + \
        [(d, "finance.integration.lab") for d in LAB_DOCUMENTS]:
    for _action in ("posted", "reversed"):
        subscribe(f"{_label_prefix}.{_action}", flag=_flag)(link_ledger)

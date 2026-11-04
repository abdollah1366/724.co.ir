"""ورود سریع کالا از شرکت: رسید انبار + (در صورت تمایل) فاکتور خرید + پرداخت، در یک فرم و یک تراکنش.

پیش از این، ورود یک بار دارو چهار سند جدا در پنل مدیریت داشت (سفارش، رسید، فاکتور، پرداخت) و هر سند باید
جداگانه ساخته، ردیف‌دار و «ثبت نهایی» می‌شد. این ماژول همان اسناد و همان کنترل‌ها را (بچ، انقضا، اصالت، شرایط
نگهداری، سقف سفارش) پشت سر هم می‌سازد؛ هیچ قاعده‌ای دور زده نمی‌شود چون ثبت هر سند از همان post() می‌گذرد.
"""

import calendar
import datetime

import jdatetime
from django.db import transaction
from django.db.models import Exists, F, OuterRef, Q, Sum

from apps.accounting.models import Party, Payment
from apps.audit.documents import DocStatus, DocumentError
from apps.core.validators import normalize_digits
from apps.inventory import services as inv

from . import services
from .models import GoodsReceipt, GoodsReceiptLine, PurchaseInvoice, PurchaseOrder, PurchaseOrderLine


def parse_expiry(value):
    """تاریخ انقضا به هر شکلی که روی جعبه یا در فرم آمده: ۱۴۰۶/۰۵/۳۱، ۱۴۰۶/۰۵، 2027/08/31، 2027-08 یا 2027-08-31.

    اگر روز نیامده باشد، پایان همان ماه در نظر گرفته می‌شود (رسم چاپ انقضا روی بسته دارو).
    """
    text = normalize_digits(value or "").strip().replace("-", "/").replace(".", "/")
    if not text:
        return None
    try:
        parts = [int(p) for p in text.split("/")]
    except ValueError:
        raise DocumentError(f"تاریخ «{value}» معتبر نیست؛ مثلاً ۱۴۰۶/۰۵ یا 2027/08 بنویسید.")
    if len(parts) not in (2, 3):
        raise DocumentError(f"تاریخ «{value}» معتبر نیست؛ مثلاً ۱۴۰۶/۰۵ یا 2027/08 بنویسید.")
    year, month = parts[0], parts[1]
    try:
        if year < 1700:  # شمسی
            if len(parts) == 3:
                return jdatetime.date(year, month, parts[2]).togregorian()
            nxt = jdatetime.date(year + (month == 12), month % 12 + 1, 1)
            return nxt.togregorian() - datetime.timedelta(days=1)
        day = parts[2] if len(parts) == 3 else calendar.monthrange(year, month)[1]
        return datetime.date(year, month, day)
    except (ValueError, TypeError):
        raise DocumentError(f"تاریخ «{value}» معتبر نیست.")


def last_cost(product):
    """آخرین بهای خرید همین کالا (پیش‌فرض ستون «بهای واحد»)؛ اگر نبود میانگین موزون فعلی."""
    line = (GoodsReceiptLine.objects.filter(product=product, document__status=DocStatus.POSTED)
            .order_by("-document__posted_at", "-pk").first())
    if line:
        return line.unit_cost
    return int(product.avg_cost or 0)


def _po_line_for(po, product, already):
    """اولین ردیف سفارش همین کالا که هنوز جا دارد؛ already = تعدادی که در همین رسید به ردیف‌ها داده شده."""
    for line in po.lines.filter(product=product).order_by("pk"):
        free = line.quantity - line.received_quantity - already.get(line.pk, 0)
        if free > 0:
            return line
    return None


@transaction.atomic
def quick_receive(*, supplier, warehouse, rows, user, delivery_note="", purchase_order=None, invoice=None, payment=None):
    """ساخت و ثبت نهایی رسید (و در صورت ارسال، فاکتور و پرداخت) در یک تراکنش؛ خطا یعنی هیچ سندی ثبت نمی‌شود.

    rows: [{"product", "quantity", "unit_cost", "batch_number", "expiry_date", "manufacture_date",
            "authenticity_code", "authenticity_verified", "location"(اختیاری)}]
    invoice: {"supplier_invoice_no", "discount", "tax"} یا None
    payment: {"method", "amount", "reference_no"} یا None
    """
    if not rows:
        raise DocumentError("هیچ کالایی در فهرست نیست.")
    receipt = GoodsReceipt.objects.create(supplier=supplier, warehouse=warehouse, purchase_order=purchase_order,
                                          delivery_note=(delivery_note or "")[:40], created_by=user)
    used = {}
    for row in rows:
        product = row["product"]
        location = row.get("location") or inv.default_location(warehouse, product)
        if location is None:
            raise DocumentError(
                f"در «{warehouse}» محلی با شرایط نگهداری «{product.get_storage_condition_display()}» برای «{product.name}» "
                "تعریف نشده است (پنل مدیریت ← محل‌های نگهداری)."
            )
        po_line = None
        if purchase_order is not None:
            po_line = _po_line_for(purchase_order, product, used)
            if po_line is None:
                raise DocumentError(f"«{product.name}» در سفارش {purchase_order.number} نیست یا تعداد سفارشش کامل رسیده است.")
            used[po_line.pk] = used.get(po_line.pk, 0) + row["quantity"]
        GoodsReceiptLine.objects.create(
            document=receipt, po_line=po_line, product=product, location=location,
            batch_number=(row.get("batch_number") or "").strip()[:60], expiry_date=row.get("expiry_date"),
            manufacture_date=row.get("manufacture_date"), quantity=row["quantity"], unit_cost=row["unit_cost"],
            authenticity_code=(row.get("authenticity_code") or "").strip()[:80],
            authenticity_verified=bool(row.get("authenticity_verified")),
        )
    receipt.post(user)
    bill = invoice_receipt(receipt, user, **invoice) if invoice else None
    paid = pay_supplier(supplier, user, **payment) if payment and payment.get("amount") else None
    return receipt, bill, paid


@transaction.atomic
def invoice_receipt(receipt, user, supplier_invoice_no, discount=0, tax=0):
    """فاکتور خرید با همان ردیف‌ها و قیمت‌های رسید (اختلاف قیمت فقط از راه تخفیف/مالیات کل)."""
    bill = PurchaseInvoice.objects.create(
        supplier=receipt.supplier, receipt=receipt, supplier_invoice_no=(supplier_invoice_no or "").strip()[:40],
        discount=discount or 0, tax=tax or 0, created_by=user,
    )
    services.fill_invoice_from_receipt(bill)
    bill.post(user)
    return bill


@transaction.atomic
def pay_supplier(supplier, user, method, amount, reference_no=""):
    payment = Payment.objects.create(direction=Payment.Direction.PAY, party=supplier, method=method, amount=amount,
                                     reference_no=(reference_no or "").strip()[:40], created_by=user)
    payment.post(user)
    return payment


# ---------------------------------------------------------------- فهرست‌های «کار مانده»
def receipts_without_invoice():
    has_invoice = PurchaseInvoice.objects.filter(receipt=OuterRef("pk"), status=DocStatus.POSTED)
    return (GoodsReceipt.objects.filter(status=DocStatus.POSTED).exclude(Exists(has_invoice))
            .select_related("supplier", "warehouse").prefetch_related("lines").order_by("posted_at"))


def open_orders():
    """سفارش‌های تأییدشده‌ای که همه ردیف‌هایشان هنوز نرسیده است."""
    received = Sum("receipt_lines__quantity", filter=Q(receipt_lines__document__status=DocStatus.POSTED))
    open_ids = (PurchaseOrderLine.objects.filter(document__status=DocStatus.POSTED).annotate(r=received)
                .filter(Q(r__isnull=True) | Q(r__lt=F("quantity"))).values("document_id"))
    return PurchaseOrder.objects.filter(pk__in=open_ids).select_related("supplier").order_by("expected_date", "date")


def supplier_balances():
    """تأمین‌کنندگانی که به آن‌ها بدهکاریم (فاکتور ثبت‌شده منهای پرداخت‌ها)."""
    rows = []
    for party in Party.objects.filter(kind=Party.Kind.SUPPLIER, is_active=True).order_by("name"):
        due = services.supplier_payable(party)
        if due:
            rows.append({"party": party, "due": due})
    return rows

from django.conf import settings
from django.db.models import Sum

from apps.accounting import services as acc
from apps.accounting.models import Party
from apps.audit.documents import DocStatus, DocumentError
from apps.inventory import services as inv
from apps.inventory.models import StockBalance, StockMove

from .models import GoodsReceiptLine, PurchaseInvoiceLine


def _rules():
    return getattr(settings, "PHARMACY_RULES", {})


def validate_receipt(receipt):
    lines = list(receipt.lines.select_related("product", "location", "po_line"))
    if not lines:
        raise DocumentError("رسید ردیف ندارد.")
    if receipt.supplier.kind != Party.Kind.SUPPLIER:
        raise DocumentError("طرف حساب رسید باید تأمین‌کننده باشد.")
    po = receipt.purchase_order
    if po is not None:
        if po.status != DocStatus.POSTED:
            raise DocumentError("سفارش خرید هنوز تأیید نشده یا لغو شده است.")
        if po.supplier_id != receipt.supplier_id:
            raise DocumentError("تأمین‌کننده رسید با سفارش خرید یکی نیست.")
    min_days = _rules().get("min_shelf_life_days_on_receipt", 0)
    serials = set()
    for line in lines:
        product = line.product
        if line.quantity <= 0:
            raise DocumentError("تعداد باید مثبت باشد.")
        inv.validate_batch_input(product, line.batch_number, line.expiry_date, receipt.date)
        if min_days and line.expiry_date and (line.expiry_date - receipt.date).days < min_days:
            raise DocumentError(f"عمر باقی‌مانده «{product.name}» کمتر از {min_days} روز است و پذیرفته نمی‌شود.")
        inv.check_location(product, receipt.warehouse, line.location)
        if product.requires_authenticity_check and not (line.authenticity_code.strip() and line.authenticity_verified):
            raise DocumentError(f"کد اصالت «{product.name}» را وارد و تأیید اصالت را علامت بزنید.")
        if product.track_serial:
            if line.quantity != 1:
                raise DocumentError(f"«{product.name}» سریالی است؛ هر ردیف فقط یک عدد.")
            key = (product.pk, line.batch_number.strip())
            exists = StockBalance.objects.filter(
                product=product, batch__number=line.batch_number.strip(), quantity__gt=0
            ).exists()
            if key in serials or exists:
                raise DocumentError(f"سریال {line.batch_number} «{product.name}» تکراری است.")
            serials.add(key)
        if po is not None:
            if line.po_line is None or line.po_line.document_id != po.pk or line.po_line.product_id != product.pk:
                raise DocumentError(f"ردیف «{product.name}» به ردیف سفارش خرید مربوط وصل نیست.")
    if po is not None:
        for po_line in po.lines.all():
            here = sum(line.quantity for line in lines if line.po_line_id == po_line.pk)
            if here and po_line.received_quantity + here > po_line.quantity:
                raise DocumentError(f"تعداد دریافتی «{po_line.product}» از تعداد سفارش بیشتر است.")


def post_receipt(receipt, user):
    source = acc.source_of(receipt)
    for line in receipt.lines.select_related("product", "location"):
        batch = inv.get_or_create_batch(line.product, line.batch_number, line.expiry_date, line.manufacture_date)
        inv.stock_in(product=line.product, warehouse=receipt.warehouse, location=line.location, batch=batch,
                     quantity=line.quantity, unit_cost=line.unit_cost, move_type=StockMove.Type.RECEIPT,
                     source=source, user=user)
    value = inv.source_inventory_value(source)
    text = f"رسید خرید {receipt.number} از {receipt.supplier}"
    acc.post_entry(source, [(acc.INVENTORY, value, 0, None, text), (acc.GRNI, 0, value, receipt.supplier, text)],
                   text, receipt.date, user)


def reverse_receipt(receipt, user):
    actual = inv.reverse_moves(acc.source_of(receipt), user)
    acc.reverse_entry_for(receipt, user, inventory_actual=actual)


def fill_invoice_from_receipt(invoice):
    """ردیف‌های فاکتور را از رسید با همان قیمت رسید پر می‌کند (قابل اصلاح پیش از ثبت)."""
    for line in invoice.receipt.lines.all():
        PurchaseInvoiceLine.objects.get_or_create(
            document=invoice, receipt_line=line, defaults={"quantity": line.quantity, "unit_price": line.unit_cost}
        )


def receipt_value(receipt):
    return inv.source_inventory_value(acc.source_of(receipt))


def validate_invoice(invoice):
    receipt = invoice.receipt
    if receipt.status != DocStatus.POSTED:
        raise DocumentError("رسید انبار این فاکتور ثبت نهایی نشده یا برگشت خورده است.")
    if receipt.supplier_id != invoice.supplier_id:
        raise DocumentError("تأمین‌کننده فاکتور با رسید یکی نیست.")
    if receipt.invoices.filter(status=DocStatus.POSTED).exclude(pk=invoice.pk).exists():
        raise DocumentError("برای این رسید قبلاً فاکتور ثبت شده است.")
    if not invoice.supplier_invoice_no.strip():
        raise DocumentError("شماره فاکتور تأمین‌کننده الزامی است.")
    lines = {line.receipt_line_id: line for line in invoice.lines.all()}
    receipt_lines = list(GoodsReceiptLine.objects.filter(document=receipt))
    if set(lines) != {line.pk for line in receipt_lines}:
        raise DocumentError("همه ردیف‌های رسید باید در فاکتور باشند (از «پر کردن از رسید» استفاده کنید).")
    for rl in receipt_lines:
        if lines[rl.pk].quantity != rl.quantity:
            raise DocumentError(f"تعداد فاکتور «{rl.product}» با رسید ({rl.quantity}) برابر نیست.")
    if invoice.discount > invoice.subtotal:
        raise DocumentError("تخفیف بیشتر از جمع فاکتور است.")


def post_invoice(invoice, user):
    source = acc.source_of(invoice)
    supplier = invoice.supplier
    grni = receipt_value(invoice.receipt)
    net = invoice.subtotal - invoice.discount
    variance = net - grni
    text = f"فاکتور خرید {invoice.supplier_invoice_no} ({invoice.number}) {supplier}"
    acc.post_entry(source, [
        (acc.GRNI, grni, 0, supplier, text),
        (acc.VAT_INPUT, invoice.tax, 0, None, text),
        (acc.PURCHASE_VARIANCE, max(variance, 0), max(-variance, 0), None, "اختلاف قیمت فاکتور با رسید"),
        (acc.AP_SUPPLIERS, 0, invoice.total, supplier, text),
    ], text, invoice.date, user)


def supplier_payable(supplier):
    agg = acc.JournalLine.objects.filter(party=supplier, account__code=acc.AP_SUPPLIERS).aggregate(d=Sum("debit"), c=Sum("credit"))
    return (agg["c"] or 0) - (agg["d"] or 0)

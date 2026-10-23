"""فروش حضوری و آنلاین، صندوق و مرجوعی. هر فروش در یک تراکنش: خروج کالا + سند حسابداری."""

from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.accounting import services as acc
from apps.accounting.models import Party
from apps.audit.context import get_actor
from apps.audit.documents import DocStatus, DocumentError
from apps.inventory import services as inv
from apps.inventory.models import StockMove, Warehouse

from .models import CashSession, SalesAllocation, SalesInvoice, SalesInvoiceLine, SalesPayment, SalesReturn

Method = SalesPayment.Method
PAYMENT_ACCOUNT = {Method.CASH: acc.CASH, Method.CARD: acc.BANK, Method.CREDIT: acc.AR_CUSTOMERS}


def _round(value):
    return int(Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _share(amount, part, whole):
    return _round(Decimal(amount) * part / whole) if whole else 0


# --- صندوق ---
def open_session(register, user, opening_cash=0):
    if CashSession.objects.filter(register=register, status=CashSession.Status.OPEN).exists():
        raise DocumentError("این صندوق یک شیفت باز دارد.")
    return CashSession.objects.create(register=register, cashier=user, opening_cash=opening_cash)


def session_expected_cash(session):
    cash_in = (
        SalesPayment.objects.filter(document__session=session, document__status=DocStatus.POSTED, method=Method.CASH)
        .aggregate(s=Sum("amount"))["s"] or 0
    )
    cash_out = (
        SalesReturn.objects.filter(session=session, status=DocStatus.POSTED, refund_method=SalesReturn.Refund.CASH)
        .aggregate(s=Sum("refund_amount"))["s"] or 0
    )
    return session.opening_cash + cash_in - cash_out


def close_session(session, counted_cash, user):
    with transaction.atomic():
        s = CashSession.objects.select_for_update().get(pk=session.pk)
        if s.status != CashSession.Status.OPEN:
            raise DocumentError("شیفت قبلاً بسته شده است.")
        if s.invoices.filter(status=DocStatus.DRAFT).exists():
            raise DocumentError("فاکتور پیش‌نویس در این شیفت مانده؛ آن را ثبت یا حذف کنید.")
        expected = session_expected_cash(s)
        s.expected_cash = expected
        s.counted_cash = counted_cash
        s.difference = counted_cash - expected
        s.status = CashSession.Status.CLOSED
        s.closed_at = timezone.now()
        s.save()
        diff = s.difference
        if diff:
            text = f"{'اضافه' if diff > 0 else 'کسری'} صندوق {s.register} شیفت {s.pk}"
            acc.post_entry(
                ("sales.cashsession", s.pk, f"شیفت {s.pk}"),
                [(acc.CASH, max(diff, 0), max(-diff, 0), None, text), (acc.CASH_OVER_SHORT, max(-diff, 0), max(diff, 0), None, text)],
                text, timezone.localdate(), user,
            )
    return s


# --- فاکتور فروش ---
def compute_taxes(invoice):
    for line in invoice.lines.select_related("product"):
        tax = _round(Decimal(line.line_total) * line.product.tax_rate / 100)
        if tax != line.tax:
            line.tax = tax
            line.save(update_fields=["tax"])


def add_line(invoice, product, quantity=1, unit_price=None):
    if not invoice.is_draft:
        raise DocumentError("فاکتور ثبت شده است.")
    if not product.is_active or not product.is_sellable or not product.price:
        raise DocumentError(f"«{product.name}» قابل فروش نیست.")
    line = invoice.lines.filter(product=product).first()
    if line:
        line.quantity += quantity
        line.save(update_fields=["quantity"])
    else:
        line = SalesInvoiceLine.objects.create(
            document=invoice, product=product, quantity=quantity, unit_price=unit_price or product.price
        )
    compute_taxes(invoice)
    return line


def validate_sale(invoice):
    compute_taxes(invoice)
    lines = list(invoice.lines.select_related("product"))
    if not lines:
        raise DocumentError("فاکتور ردیف ندارد.")
    online = invoice.channel == SalesInvoice.Channel.ONLINE
    for line in lines:
        product = line.product
        if line.quantity <= 0 or line.unit_price <= 0:
            raise DocumentError(f"تعداد و قیمت «{product.name}» باید مثبت باشد.")
        if line.line_discount > line.quantity * line.unit_price:
            raise DocumentError(f"تخفیف «{product.name}» از مبلغ ردیف بیشتر است.")
        if not product.is_sellable:
            raise DocumentError(f"فروش «{product.name}» متوقف شده است.")
        if product.max_qty_per_sale and line.quantity > product.max_qty_per_sale:
            raise DocumentError(f"حداکثر تعداد مجاز «{product.name}» در هر فروش {product.max_qty_per_sale} است.")
        if online and (product.requires_prescription or product.is_controlled):
            raise DocumentError(f"«{product.name}» نیازمند نسخه است و فروش آنلاین آن مجاز نیست.")
    needs_rx = any(line.product.requires_prescription for line in lines)
    if needs_rx or invoice.insurance_amount:
        if not invoice.prescription_number.strip() or not invoice.prescriber_council_no.strip():
            raise DocumentError("برای داروی نسخه‌ای یا فروش بیمه‌ای، شماره نسخه و شماره نظام پزشکی تجویزکننده الزامی است.")
    if invoice.discount > invoice.subtotal:
        raise DocumentError("تخفیف فاکتور از جمع فاکتور بیشتر است.")
    if invoice.insurance_amount:
        if not invoice.insurer_id:
            raise DocumentError("بیمه‌گر را انتخاب کنید.")
        if invoice.insurance_amount > invoice.total:
            raise DocumentError("سهم بیمه از مبلغ فاکتور بیشتر است.")
    payments = list(invoice.payments.all())
    if any(p.method == Method.CREDIT for p in payments) and not invoice.customer_id:
        raise DocumentError("برای فروش نسیه، مشتری را مشخص کنید.")
    if sum(p.amount for p in payments) + invoice.insurance_amount != invoice.total:
        raise DocumentError(
            f"جمع پرداخت‌ها و سهم بیمه ({sum(p.amount for p in payments) + invoice.insurance_amount}) "
            f"با مبلغ فاکتور ({invoice.total}) برابر نیست."
        )


def post_sale(invoice, user):
    online = invoice.channel == SalesInvoice.Channel.ONLINE
    if not online:
        session = invoice.session
        if session is None or session.status != CashSession.Status.OPEN:
            raise DocumentError("فروش حضوری فقط در شیفت باز صندوق ممکن است.")
        if session.register.warehouse_id != invoice.warehouse_id:
            raise DocumentError("انبار فاکتور با انبار صندوق یکی نیست.")
    if invoice.needs_prescription:
        if user is None or not user.has_perm("sales.approve_prescription"):
            raise DocumentError("فروش داروی نسخه‌ای نیازمند تأیید داروساز (مجوز تأیید نسخه) است.")
        invoice.pharmacist_approved_by = user
        invoice._save_status(["pharmacist_approved_by"])
    source = acc.source_of(invoice)
    cost = 0
    for line in invoice.lines.select_related("product"):
        moves = inv.stock_out(
            product=line.product, warehouse=invoice.warehouse, quantity=line.quantity, move_type=StockMove.Type.SALE,
            source=source, user=user, sellable_only=True, from_reserved=online,
        )
        for move in moves:
            SalesAllocation.objects.create(
                line=line, batch=move.batch, location=move.location, quantity=-move.quantity, cost_total=-move.total_cost
            )
        line.cost_total = -sum(m.total_cost for m in moves)
        line._system_write = True
        line.save(update_fields=["cost_total"])
        cost += line.cost_total
    text = f"{invoice.get_channel_display()} {invoice.number}"
    lines = [
        (PAYMENT_ACCOUNT[p.method], p.amount, 0, invoice.customer if p.method == Method.CREDIT else None, text)
        for p in invoice.payments.all()
    ]
    lines += [
        (acc.AR_INSURERS, invoice.insurance_amount, 0, invoice.insurer, f"سهم بیمه {text}"),
        (acc.SALES, 0, invoice.subtotal - invoice.discount, None, text),
        (acc.VAT_OUTPUT, 0, invoice.tax, None, text),
        (acc.COGS, cost, 0, None, f"بهای تمام‌شده {text}"),
        (acc.INVENTORY, 0, cost, None, f"بهای تمام‌شده {text}"),
    ]
    acc.post_entry(source, lines, text, invoice.date, user)


def reverse_sale(invoice, user):
    actual = inv.reverse_moves(acc.source_of(invoice), user)
    acc.reverse_entry_for(invoice, user, inventory_actual=actual)


def online_warehouse():
    code = getattr(settings, "PHARMACY_RULES", {}).get("online_warehouse_code")
    if code:
        return Warehouse.objects.get(code=code)
    warehouse = inv.default_warehouse()
    if warehouse is None:
        raise DocumentError("انبار فروش تعریف نشده است.")
    return warehouse


def customer_party(user):
    party = Party.objects.filter(user=user).first()
    if party:
        return party
    profile = getattr(user, "profile", None)
    return Party.objects.create(
        kind=Party.Kind.CUSTOMER, name=user.get_full_name() or user.get_username(), user=user,
        mobile=(profile.mobile or "") if profile else "",
    )


def fulfill_online_order(order):
    """تحویل سفارش آنلاین: فاکتور فروش آنلاین با پرداخت در محل و خروج کالا از موجودی رزروشده."""
    if SalesInvoice.objects.filter(order=order).exists():
        return order.invoice
    items = [item for item in order.items.select_related("product") if item.product_id]
    if not items:
        return None
    user = get_actor()[0]
    invoice = SalesInvoice.objects.create(
        channel=SalesInvoice.Channel.ONLINE, order=order, warehouse=online_warehouse(), customer=customer_party(order.user),
        created_by=user, note=f"سفارش آنلاین {order.code}",
    )
    for item in items:
        SalesInvoiceLine.objects.create(document=invoice, product=item.product, quantity=item.quantity, unit_price=item.unit_price)
    compute_taxes(invoice)
    SalesPayment.objects.create(document=invoice, method=Method.CASH, amount=invoice.total)
    invoice.post(user)
    return invoice


def create_pos_invoice(session, user, client_token=None):
    if session.status != CashSession.Status.OPEN:
        raise DocumentError("شیفت صندوق بسته است.")
    return SalesInvoice.objects.create(
        channel=SalesInvoice.Channel.POS, session=session, warehouse=session.register.warehouse, created_by=user,
        client_token=client_token or None,
    )


# --- مرجوعی ---
def validate_return(ret):
    invoice = ret.invoice
    if invoice.status != DocStatus.POSTED:
        raise DocumentError("فقط از فاکتور ثبت‌شده و ابطال‌نشده می‌توان مرجوعی گرفت.")
    lines = list(ret.lines.select_related("invoice_line__product"))
    if not lines:
        raise DocumentError("مرجوعی ردیف ندارد.")
    if not ret.reason.strip():
        raise DocumentError("علت مرجوعی را وارد کنید.")
    if ret.refund_method == SalesReturn.Refund.CREDIT and not invoice.customer_id:
        raise DocumentError("فاکتور مشتری مشخص ندارد؛ استرداد را نقد یا کارتی انجام دهید.")
    for line in lines:
        il = line.invoice_line
        if il.document_id != invoice.pk:
            raise DocumentError("ردیف مرجوعی متعلق به این فاکتور نیست.")
        if line.quantity <= 0 or line.quantity > il.quantity - il.returned_quantity:
            raise DocumentError(f"تعداد مرجوعی «{il.product}» بیشتر از مانده قابل برگشت ({il.quantity - il.returned_quantity}) است.")


def _quarantine():
    code = getattr(settings, "PHARMACY_RULES", {}).get("quarantine_warehouse_code", "QRN")
    warehouse = Warehouse.objects.filter(code=code).first()
    if warehouse is None:
        raise DocumentError("انبار قرنطینه تعریف نشده است.")
    return warehouse


def post_return(ret, user):
    invoice = ret.invoice
    source = acc.source_of(ret)
    net_total = tax_total = cost_total = 0
    for line in ret.lines.select_related("invoice_line__product"):
        il = line.invoice_line
        product = il.product
        net = _share(il.line_total, line.quantity, il.quantity)
        net -= _share(invoice.discount, net, invoice.subtotal)
        net_total += net
        tax_total += _share(il.tax, line.quantity, il.quantity)
        remaining = line.quantity
        for alloc in il.allocations.select_related("batch", "location").order_by("id"):
            free = alloc.quantity - alloc.returned_quantity
            take = min(free, remaining)
            if take <= 0:
                continue
            if take == free:
                value = alloc.cost_total - alloc.returned_cost
            else:
                value = _share(alloc.cost_total, take, alloc.quantity)
            if ret.restock and alloc.batch.is_sellable:
                warehouse, location = invoice.warehouse, alloc.location
            else:
                warehouse = _quarantine()
                location = inv.default_location(warehouse, product)
                if location is None:
                    raise DocumentError(f"در انبار قرنطینه محلی با شرایط نگهداری «{product.get_storage_condition_display()}» نیست.")
            inv.stock_in(product=product, warehouse=warehouse, location=location, batch=alloc.batch, quantity=take,
                         unit_cost=Decimal(value) / take, total_cost=value, move_type=StockMove.Type.SALE_RETURN,
                         source=source, user=user)
            alloc.returned_quantity += take
            alloc.returned_cost += value
            alloc.save(update_fields=["returned_quantity", "returned_cost"])
            cost_total += value
            remaining -= take
            if remaining == 0:
                break
    refund_total = net_total + tax_total
    insurer_part = _share(refund_total, invoice.insurance_amount, invoice.total) if invoice.insurance_amount else 0
    customer_part = refund_total - insurer_part
    refund_account = {SalesReturn.Refund.CASH: acc.CASH, SalesReturn.Refund.CARD: acc.BANK}.get(ret.refund_method, acc.AR_CUSTOMERS)
    text = f"مرجوعی {ret.number} از فاکتور {invoice.number}"
    acc.post_entry(source, [
        (acc.SALES_RETURNS, net_total, 0, None, text),
        (acc.VAT_OUTPUT, tax_total, 0, None, text),
        (acc.AR_INSURERS, 0, insurer_part, invoice.insurer, f"سهم بیمه {text}"),
        (refund_account, 0, customer_part, invoice.customer if refund_account == acc.AR_CUSTOMERS else None, text),
        (acc.INVENTORY, cost_total, 0, None, text),
        (acc.COGS, 0, cost_total, None, text),
    ], text, ret.date, user)
    ret.refund_amount = customer_part
    ret._save_status(["refund_amount"])

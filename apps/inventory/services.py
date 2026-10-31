"""موتور انبار: ورود و خروج کالا با قفل ردیف، تخصیص به ترتیب نزدیک‌ترین انقضا (FEFO) و میانگین موزون متحرک.

قواعد:
- هر تغییر موجودی فقط از این توابع و همیشه همراه یک سند مرجع ثبت‌شده انجام می‌شود.
- ردیف کالا و ردیف‌های مانده با select_for_update قفل می‌شوند تا دو فروش هم‌زمان یک موجودی را مصرف نکنند.
- مانده منفی با قید پایگاه داده غیرممکن است.
- Product.stock = موجودی سالم انبارهای فروش منهای رزرو سفارش‌های آنلاین باز (برای سایت و صندوق).
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.accounting import services as acc
from apps.audit import log
from apps.audit.documents import DocumentError
from apps.audit.models import AuditLog
from apps.shop.models import Order, OrderItem, Product

from .models import Batch, BatchStatusChange, InventoryDocument, Location, StockBalance, StockMove, Warehouse

OPEN_ORDER_STATUSES = [Order.Status.PENDING, Order.Status.CONFIRMED, Order.Status.READY]
FOUR_PLACES = Decimal("0.0001")


class StockError(DocumentError):
    pass


class InsufficientStock(StockError):
    pass


def _round(value):
    return int(Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def source_of(doc):
    return doc._meta.label_lower, doc.pk, getattr(doc, "number", "") or ""


def lock_product(product_id):
    return Product.objects.select_for_update().get(pk=product_id)


def product_totals(product_id):
    agg = StockMove.objects.filter(product_id=product_id).aggregate(q=Sum("quantity"), v=Sum("total_cost"))
    return agg["q"] or 0, agg["v"] or 0


def _counts_available(warehouse, batch):
    # تاریخ انقضا با دستور روزانه expire_batches (و پیش از هر فروش) به وضعیت «منقضی» تبدیل می‌شود.
    return warehouse.is_sellable and batch.status == Batch.Status.ACTIVE


def _set_available(product, delta):
    if not delta:
        return
    product.stock = max(0, product.stock + delta)
    Product.objects.filter(pk=product.pk).update(stock=product.stock)


# --- بچ ---
def validate_batch_input(product, number, expiry, ref_date, allow_expired=False):
    name = product.name
    if product.track_batch and not (number or "").strip():
        raise StockError(f"شماره بچ/سریال برای «{name}» الزامی است.")
    if product.track_expiry and not expiry:
        raise StockError(f"تاریخ انقضا برای «{name}» الزامی است.")
    if expiry and expiry <= ref_date and not allow_expired:
        raise StockError(f"تاریخ انقضای «{name}» گذشته است؛ کالای منقضی وارد انبار فروش نمی‌شود.")


def get_or_create_batch(product, number, expiry=None, manufacture=None):
    number = (number or "").strip() if product.track_batch else Batch.GENERIC_NUMBER
    batch, created = Batch.objects.get_or_create(
        product=product, number=number, defaults={"expiry_date": expiry, "manufacture_date": manufacture}
    )
    if not created and product.track_expiry and expiry and batch.expiry_date and batch.expiry_date != expiry:
        raise StockError(
            f"تاریخ انقضای بچ {number} «{product.name}» با سابقه قبلی ({batch.expiry_date}) مغایرت دارد. "
            "اطلاعات بچ را بررسی کنید."
        )
    if not created and batch.expiry_date is None and expiry:
        Batch.objects.filter(pk=batch.pk).update(expiry_date=expiry)
        batch.expiry_date = expiry
    return batch


def find_batch(product, number):
    number = (number or "").strip() if product.track_batch else Batch.GENERIC_NUMBER
    batch = Batch.objects.filter(product=product, number=number).first()
    if batch is None:
        raise StockError(f"بچ {number} برای «{product.name}» پیدا نشد.")
    return batch


def check_location(product, warehouse, location):
    if location.warehouse_id != warehouse.pk:
        raise StockError(f"محل «{location}» متعلق به انبار «{warehouse}» نیست.")
    if location.storage_condition != product.storage_condition:
        raise StockError(
            f"«{product.name}» باید در شرایط «{product.get_storage_condition_display()}» نگهداری شود، "
            f"اما محل «{location.name}» برای «{location.get_storage_condition_display()}» است."
        )


# --- ورود و خروج ---
def stock_in(*, product, warehouse, location, batch, quantity, unit_cost, move_type, source, user,
             is_reversal=False, keep_avg=False, total_cost=None):
    if quantity <= 0:
        raise StockError("تعداد ورود باید مثبت باشد.")
    check_location(product, warehouse, location)
    with transaction.atomic():
        p = lock_product(product.pk)
        qty0, value0 = product_totals(p.pk)
        unit_cost = Decimal(unit_cost)
        value_in = _round(quantity * unit_cost) if total_cost is None else int(total_cost)
        qty1 = qty0 + quantity
        if keep_avg and qty0 > 0:
            avg = p.avg_cost
        else:
            avg = (Decimal(value0 + value_in) / qty1).quantize(FOUR_PLACES) if qty1 > 0 else unit_cost
        balance, _ = StockBalance.objects.get_or_create(product=p, warehouse=warehouse, location=location, batch=batch)
        balance = StockBalance.objects.select_for_update().get(pk=balance.pk)
        balance.quantity += quantity
        balance.save(update_fields=["quantity"])
        source_type, source_id, source_number = source
        move = StockMove.objects.create(
            move_type=move_type, is_reversal=is_reversal, product=p, warehouse=warehouse, location=location, batch=batch,
            quantity=quantity, unit_cost=unit_cost.quantize(FOUR_PLACES), total_cost=value_in, product_qty_after=qty1,
            avg_cost_after=avg, source_type=source_type, source_id=source_id, source_number=source_number, user=user,
        )
        if avg != p.avg_cost:
            p.avg_cost = avg
            Product.objects.filter(pk=p.pk).update(avg_cost=avg)
        if _counts_available(warehouse, batch):
            _set_available(p, quantity)
    return move


def stock_out(*, product, warehouse, quantity, move_type, source, user, batch=None, location=None,
              sellable_only=False, from_reserved=False, is_reversal=False):
    """خروج کالا. بدون تعیین بچ، از بچ‌های با نزدیک‌ترین انقضا برداشته می‌شود (FEFO)."""
    if quantity <= 0:
        raise StockError("تعداد خروج باید مثبت باشد.")
    with transaction.atomic():
        p = lock_product(product.pk)
        if sellable_only:
            expire_batches(product=p)
            p.refresh_from_db(fields=["stock"])
        qs = (
            StockBalance.objects.select_for_update().filter(product=p, warehouse=warehouse, quantity__gt=0)
            .select_related("batch", "location")
        )
        if batch is not None:
            qs = qs.filter(batch=batch)
        if location is not None:
            qs = qs.filter(location=location)
        balances = list(qs)
        if sellable_only:
            balances = [b for b in balances if _counts_available(warehouse, b.batch)]
        balances.sort(key=lambda b: (b.batch.expiry_date is None, b.batch.expiry_date or timezone.localdate(), b.batch_id))
        if sum(b.quantity for b in balances) < quantity:
            raise InsufficientStock(f"موجودی «{p.name}» در «{warehouse}» کافی نیست.")
        if sellable_only and not from_reserved and p.stock < quantity:
            raise InsufficientStock(f"موجودی آزاد «{p.name}» کافی نیست (بخشی از موجودی برای سفارش آنلاین رزرو است).")
        qty0, value0 = product_totals(p.pk)
        avg = p.avg_cost
        source_type, source_id, source_number = source
        moves, remaining, available_delta = [], quantity, 0
        for balance in balances:
            take = min(balance.quantity, remaining)
            balance.quantity -= take
            balance.save(update_fields=["quantity"])
            qty_after = qty0 - take
            # آخرین واحد کل ارزش باقی‌مانده را می‌برد تا گردکردن، ارزش یتیم باقی نگذارد.
            cost = value0 if qty_after == 0 else _round(take * avg)
            value0 -= cost
            qty0 = qty_after
            moves.append(StockMove.objects.create(
                move_type=move_type, is_reversal=is_reversal, product=p, warehouse=warehouse, location=balance.location,
                batch=balance.batch, quantity=-take, unit_cost=avg, total_cost=-cost, product_qty_after=qty_after,
                avg_cost_after=avg, source_type=source_type, source_id=source_id, source_number=source_number, user=user,
            ))
            if _counts_available(warehouse, balance.batch) and not from_reserved:
                available_delta -= take
            remaining -= take
            if remaining == 0:
                break
        _set_available(p, available_delta)
    return moves


def reverse_moves(source, user):
    """حرکت‌های معکوس همه حرکات یک سند. خروجی: جمع ارزش حرکات برگشتی (با علامت)."""
    source_type, source_id, _ = source
    total = 0
    originals = StockMove.objects.filter(source_type=source_type, source_id=source_id, is_reversal=False).order_by("-id")
    for move in originals.select_related("product", "warehouse", "location", "batch"):
        if move.quantity > 0:
            outs = stock_out(
                product=move.product, warehouse=move.warehouse, quantity=move.quantity, move_type=move.move_type,
                source=source, user=user, batch=move.batch, location=move.location, is_reversal=True,
            )
            total += sum(m.total_cost for m in outs)
        else:
            back = stock_in(
                product=move.product, warehouse=move.warehouse, location=move.location, batch=move.batch,
                quantity=-move.quantity, unit_cost=move.unit_cost, total_cost=-move.total_cost, move_type=move.move_type,
                source=source, user=user, is_reversal=True,
            )
            total += back.total_cost
    return total


def source_inventory_value(source):
    agg = StockMove.objects.filter(source_type=source[0], source_id=source[1]).aggregate(v=Sum("total_cost"))
    return agg["v"] or 0


# --- وضعیت بچ: قرنطینه، فراخوان، توقیف، انقضا ---
def change_batch_status(batch, new_status, reason, user, reference=""):
    if not (reason or "").strip():
        raise StockError("علت تغییر وضعیت را وارد کنید.")
    with transaction.atomic():
        p = lock_product(batch.product_id)
        b = Batch.objects.select_for_update().get(pk=batch.pk)
        if b.status == new_status:
            return b
        if new_status == Batch.Status.ACTIVE and b.is_expired:
            raise StockError(f"بچ {b.number} منقضی شده و قابل آزادسازی برای فروش نیست.")
        was_sellable = b.status == Batch.Status.ACTIVE
        old = b.status
        b.status = new_status
        Batch.objects.filter(pk=b.pk).update(status=new_status)
        BatchStatusChange.objects.create(
            batch=b, old_status=old, new_status=new_status, reason=reason.strip(), reference=reference, user=user
        )
        now_sellable = new_status == Batch.Status.ACTIVE
        if was_sellable != now_sellable:
            qty = (
                StockBalance.objects.filter(batch=b, warehouse__is_sellable=True).aggregate(q=Sum("quantity"))["q"] or 0
            )
            _set_available(p, qty if now_sellable else -qty)
        log.record(AuditLog.Action.STATUS, b, message=f"{old} ← {new_status}: {reason}", user=user)
    batch.status = new_status
    return b


def expire_batches(user=None, product=None):
    """بچ‌های تاریخ‌گذشته را «منقضی» می‌کند تا از موجودی قابل فروش خارج شوند (روزانه اجرا شود)."""
    today = timezone.localdate()
    count = 0
    batches = Batch.objects.filter(status=Batch.Status.ACTIVE, expiry_date__lt=today)
    if product is not None:
        batches = batches.filter(product=product)
    for batch in batches:
        change_batch_status(batch, Batch.Status.EXPIRED, "انقضای خودکار", user)
        count += 1
    return count


# --- تطبیق موجودی قابل فروش ---
def sellable_on_hand(product):
    return (
        StockBalance.objects.filter(product=product, warehouse__is_sellable=True, batch__status=Batch.Status.ACTIVE)
        .aggregate(q=Sum("quantity"))["q"] or 0
    )


def reserved_quantity(product):
    return (
        OrderItem.objects.filter(product=product, order__status__in=OPEN_ORDER_STATUSES)
        .aggregate(q=Sum("quantity"))["q"] or 0
    )


def reconcile_available(fix=False):
    """مقایسه Product.stock با «موجودی سالم - رزرو». خروجی: فهرست اختلاف‌ها."""
    diffs = []
    for product in Product.objects.all():
        expected = max(0, sellable_on_hand(product) - reserved_quantity(product))
        if product.stock != expected:
            diffs.append((product, product.stock, expected))
            if fix:
                Product.objects.filter(pk=product.pk).update(stock=expected)
    return diffs


def on_hand(product, warehouse=None):
    qs = StockBalance.objects.filter(product=product)
    if warehouse:
        qs = qs.filter(warehouse=warehouse)
    return qs.aggregate(q=Sum("quantity"))["q"] or 0


# --- اسناد انبار ---
def validate_inventory_document(doc):
    lines = list(doc.lines.select_related("product", "location"))
    if not lines:
        raise StockError("سند ردیف ندارد.")
    kind = doc.kind
    if kind == InventoryDocument.Kind.TRANSFER and not doc.dest_warehouse_id:
        raise StockError("انبار مقصد را مشخص کنید.")
    for line in lines:
        if line.quantity <= 0:
            raise StockError("تعداد باید مثبت باشد.")
        if line.location.warehouse_id != doc.warehouse_id:
            raise StockError(f"محل ردیف «{line.product}» در انبار سند نیست.")
        if kind in InventoryDocument.INBOUND:
            if line.unit_cost is None:
                raise StockError(f"بهای واحد «{line.product}» را وارد کنید.")
            validate_batch_input(line.product, line.batch_number, line.expiry_date, doc.date)
            if line.product.track_serial and line.quantity != 1:
                raise StockError(f"«{line.product}» با سریال ردیابی می‌شود؛ هر ردیف فقط یک عدد.")
        elif line.product.track_batch and not line.batch_number.strip():
            raise StockError(f"شماره بچ «{line.product}» را برای خروج مشخص کنید.")
        if kind == InventoryDocument.Kind.TRANSFER:
            if not line.dest_location_id or line.dest_location.warehouse_id != doc.dest_warehouse_id:
                raise StockError(f"محل مقصد «{line.product}» باید در انبار مقصد باشد.")
            if line.dest_location_id == line.location_id:
                raise StockError("مبدأ و مقصد انتقال یکسان است.")


JOURNAL_ACCOUNTS = {
    InventoryDocument.Kind.OPENING: acc.OPENING_EQUITY,
    InventoryDocument.Kind.ADJUST_IN: acc.INVENTORY_GAIN,
    InventoryDocument.Kind.ADJUST_OUT: acc.INVENTORY_LOSS,
    InventoryDocument.Kind.WASTE: acc.INVENTORY_LOSS,
    InventoryDocument.Kind.CONSUME: acc.CONSUMPTION,
}


def post_inventory_document(doc, user):
    source = source_of(doc)
    Kind = InventoryDocument.Kind
    for line in doc.lines.select_related("product", "location", "dest_location"):
        product = line.product
        if doc.kind in InventoryDocument.INBOUND:
            batch = get_or_create_batch(product, line.batch_number, line.expiry_date)
            stock_in(product=product, warehouse=doc.warehouse, location=line.location, batch=batch,
                     quantity=line.quantity, unit_cost=line.unit_cost, move_type=doc.kind, source=source, user=user)
        elif doc.kind == Kind.TRANSFER:
            batch = find_batch(product, line.batch_number)
            outs = stock_out(product=product, warehouse=doc.warehouse, quantity=line.quantity, batch=batch,
                             location=line.location, move_type="transfer_out", source=source, user=user)
            for out in outs:
                stock_in(product=product, warehouse=doc.dest_warehouse, location=line.dest_location, batch=batch,
                         quantity=-out.quantity, unit_cost=out.unit_cost, total_cost=-out.total_cost,
                         move_type="transfer_in", source=source, user=user, keep_avg=True)
        else:
            batch = find_batch(product, line.batch_number)
            stock_out(product=product, warehouse=doc.warehouse, quantity=line.quantity, batch=batch,
                      location=line.location, move_type=doc.kind, source=source, user=user)
    value = source_inventory_value(source)
    if doc.kind in JOURNAL_ACCOUNTS and value:
        counter = JOURNAL_ACCOUNTS[doc.kind]
        text = f"{doc.get_kind_display()} {doc.number} — {doc.warehouse}"
        # مصرف داخلی به مرکز هزینه همان بخش (مثلاً اتاق پانسمان ← درمانگاه) می‌رود.
        center = doc.warehouse.cost_center.code if doc.kind == InventoryDocument.Kind.CONSUME and doc.warehouse.cost_center_id else None
        acc.post_entry(source, [(acc.INVENTORY, value, 0, None, text), (counter, 0, value, None, text)], text, doc.date, user,
                       cost_center=center)


def reverse_inventory_document(doc, user):
    source = source_of(doc)
    if doc.kind == InventoryDocument.Kind.TRANSFER:
        # ورودِ مقصد برگشت و همان ارزش به مبدأ برمی‌گردد (بدون اثر بر میانگین).
        for move in StockMove.objects.filter(source_type=source[0], source_id=source[1], move_type="transfer_in",
                                             is_reversal=False).select_related("product", "batch"):
            outs = stock_out(product=move.product, warehouse=move.warehouse, quantity=move.quantity, batch=move.batch,
                             location=move.location, move_type="transfer_out", source=source, user=user, is_reversal=True)
            original_out = StockMove.objects.filter(
                source_type=source[0], source_id=source[1], move_type="transfer_out", is_reversal=False,
                product=move.product, batch=move.batch,
            ).first()
            for out in outs:
                stock_in(product=move.product, warehouse=original_out.warehouse, location=original_out.location,
                         batch=move.batch, quantity=-out.quantity, unit_cost=out.unit_cost, total_cost=-out.total_cost,
                         move_type="transfer_in", source=source, user=user, is_reversal=True, keep_avg=True)
        return
    actual = reverse_moves(source, user)
    acc.reverse_entry_for(doc, user, inventory_actual=actual)


def default_warehouse():
    return Warehouse.objects.filter(is_active=True, is_sellable=True).order_by("code").first()


def default_location(warehouse, product):
    return (
        Location.objects.filter(warehouse=warehouse, is_active=True, storage_condition=product.storage_condition)
        .order_by("code").first()
    )

"""درخواست کالا از بخش‌ها و تحویل از انبار تأمین، و مصرف داخلی بخش.

گردش: مسئول بخش (مثلاً اتاق پانسمان) درخواست می‌دهد ← انباردار «تحویل» می‌زند ← سند انتقال با بچ‌های نزدیک‌ترین انقضا
(FEFO) ساخته و ثبت می‌شود ← بخش هنگام استفاده، «مصرف داخلی» ثبت می‌کند که هزینه را به مرکز هزینه همان بخش می‌برد.
"""

from django.db import transaction
from django.utils import timezone

from apps.audit.documents import DocStatus, DocumentError

from . import services as inv
from .models import Batch, InventoryDocument, InventoryDocumentLine, Location, StockBalance, StockRequest, StockRequestLine, Warehouse


def can_act_for(user, warehouse):
    """مسئولان بخش؛ اگر مسئولی تعیین نشده، هر کسی که مجوز ثبت سند انبار دارد."""
    if user.is_superuser:
        return True
    keepers = warehouse.keepers.all()
    return keepers.filter(pk=user.pk).exists() if keepers.exists() else True


def validate_request(req):
    lines = list(req.lines.select_related("product"))
    if not lines:
        raise DocumentError("درخواست ردیف ندارد.")
    if req.requester_id == req.source_id:
        raise DocumentError("بخش درخواست‌کننده و انبار تأمین یکسان است.")
    if not (req.requester.is_active and req.source.is_active):
        raise DocumentError("انبار غیرفعال است.")
    if req.source.kind == Warehouse.Kind.QUARANTINE:
        raise DocumentError("از انبار قرنطینه نمی‌توان کالا تحویل داد.")
    for line in lines:
        if line.quantity <= 0:
            raise DocumentError("تعداد باید مثبت باشد.")


def create_request(requester, items, user, source=None, reason="", post=True):
    """items: [(product, quantity)]."""
    source = source or requester.supplies_from
    if source is None:
        raise DocumentError(f"برای «{requester}» انبار تأمین مشخص نشده است.")
    with transaction.atomic():
        req = StockRequest.objects.create(requester=requester, source=source, reason=reason[:300], created_by=user)
        merged = {}
        for product, qty in items:
            merged[product] = merged.get(product, 0) + int(qty)
        for product, qty in merged.items():
            StockRequestLine.objects.create(document=req, product=product, quantity=qty)
        if post:
            req.post(user)
    return req


def _healthy(batch, today):
    return batch.status == Batch.Status.ACTIVE and not (batch.expiry_date and batch.expiry_date < today)


def plan_fefo(product, warehouse, quantity, location=None, batch=None, healthy_only=True):
    """تخصیص به ترتیب نزدیک‌ترین انقضا: [(بچ، محل، تعداد)] و کسری. پیش‌فرض فقط بچ‌های سالم و منقضی‌نشده."""
    today = timezone.localdate()
    qs = StockBalance.objects.filter(product=product, warehouse=warehouse, quantity__gt=0).select_related("batch", "location")
    if location is not None:
        qs = qs.filter(location=location)
    if batch is not None:
        qs = qs.filter(batch=batch)
    balances = [b for b in qs if not healthy_only or _healthy(b.batch, today)]
    balances.sort(key=lambda b: (b.batch.expiry_date is None, b.batch.expiry_date or today, b.batch_id, b.location.code))
    plan, remaining = [], quantity
    for balance in balances:
        take = min(balance.quantity, remaining)
        if take:
            plan.append((balance.batch, balance.location, take))
            remaining -= take
        if not remaining:
            break
    return plan, remaining


def destination_location(warehouse, product):
    location = inv.default_location(warehouse, product)
    if location is None:
        raise DocumentError(
            f"در «{warehouse}» محلی با شرایط «{product.get_storage_condition_display()}» برای «{product.name}» تعریف نشده است."
        )
    return location


def issue_request(req, user, quantities=None):
    """تحویل درخواست: سند انتقال FEFO می‌سازد و ثبت می‌کند. quantities: {شناسه ردیف: تعداد} (پیش‌فرض: کل مانده).

    خروجی: (سند انتقال، {کالا: کسری}). اگر هیچ موجودی‌ای نباشد خطا می‌دهد و چیزی ثبت نمی‌شود.
    """
    quantities = quantities or {}
    with transaction.atomic():
        req = StockRequest.objects.select_for_update().select_related("source", "requester").get(pk=req.pk)
        if req.status != DocStatus.POSTED or req.progress in (StockRequest.Progress.DONE, StockRequest.Progress.CLOSED):
            raise DocumentError("این درخواست قابل تحویل نیست.")
        doc = InventoryDocument.objects.create(
            kind=InventoryDocument.Kind.TRANSFER, warehouse=req.source, dest_warehouse=req.requester, stock_request=req,
            reason=f"تحویل درخواست {req.number} به {req.requester}"[:300], created_by=user,
        )
        issued, shortages = {}, {}
        for line in req.lines.select_related("product"):
            wanted = min(int(quantities.get(line.pk, line.remaining)), line.remaining)
            if wanted <= 0:
                continue
            plan, short = plan_fefo(line.product, req.source, wanted)
            if short:
                shortages[line.product] = short
            dest = destination_location(req.requester, line.product) if plan else None
            for batch, location, qty in plan:
                InventoryDocumentLine.objects.create(
                    document=doc, product=line.product, batch_number=batch.number, expiry_date=batch.expiry_date,
                    location=location, dest_location=dest, quantity=qty,
                )
                issued[line.pk] = issued.get(line.pk, 0) + qty
        if not issued:
            raise DocumentError("موجودی سالم برای تحویل این درخواست در انبار تأمین نیست.")
        doc.post(user)
        for line in req.lines.all():
            if line.pk in issued:
                line.issued_quantity += issued[line.pk]
                line._system_write = True
                line.save(update_fields=["issued_quantity"])
        remaining = sum(line.remaining for line in req.lines.all())
        req.progress = StockRequest.Progress.DONE if remaining == 0 else StockRequest.Progress.PARTIAL
        req._save_status(["progress"])
    return doc, shortages


def close_request(req, user):
    """بستن درخواستی که بقیه‌اش تحویل نمی‌شود (مثلاً کالا تمام شده)."""
    if req.status != DocStatus.POSTED or req.progress == StockRequest.Progress.DONE:
        raise DocumentError("این درخواست باز نیست.")
    req.progress = StockRequest.Progress.CLOSED
    req._save_status(["progress"])
    return req


def build_document(kind, warehouse, items, user, dest_warehouse=None, reason="", post=True):
    """سند انبار از ردیف‌های اسکن‌شده. items: [{"product", "quantity", "batch"(اختیاری), "location"(اختیاری)}].

    برای خروج (انتقال، مصرف، ضایعات) اگر بچ مشخص نباشد بچ‌ها FEFO انتخاب و ردیف‌ها شکسته می‌شوند.
    """
    Kind = InventoryDocument.Kind
    if kind not in (Kind.TRANSFER, Kind.CONSUME, Kind.WASTE):
        raise DocumentError("نوع سند پشتیبانی نمی‌شود.")
    if kind == Kind.TRANSFER and (dest_warehouse is None or dest_warehouse == warehouse):
        raise DocumentError("انبار مقصد را درست انتخاب کنید.")
    if kind == Kind.WASTE and not reason.strip():
        raise DocumentError("علت ضایعات را بنویسید.")
    with transaction.atomic():
        doc = InventoryDocument.objects.create(kind=kind, warehouse=warehouse, dest_warehouse=dest_warehouse if kind == Kind.TRANSFER else None,
                                               reason=reason[:300], created_by=user)
        for item in items:
            product, qty = item["product"], int(item["quantity"])
            batch = item.get("batch")
            # مصرف فقط از بچ سالم؛ ضایعات و انتقالِ بچ مشخص (مثلاً منقضی به قرنطینه) هر وضعیتی را می‌پذیرد.
            healthy_only = kind == Kind.CONSUME or (kind == Kind.TRANSFER and batch is None)
            plan, short = plan_fefo(product, warehouse, qty, location=item.get("location"), batch=batch,
                                    healthy_only=healthy_only)
            if short:
                raise DocumentError(f"موجودی «{product.name}» در «{warehouse}» کافی نیست ({short} عدد کسری).")
            dest = destination_location(dest_warehouse, product) if kind == Kind.TRANSFER else None
            for b, location, q in plan:
                InventoryDocumentLine.objects.create(document=doc, product=product, batch_number=b.number,
                                                     expiry_date=b.expiry_date, location=location, dest_location=dest, quantity=q)
        if post:
            doc.post(user)
    return doc


def default_location(warehouse):
    return Location.objects.filter(warehouse=warehouse, is_active=True).order_by("code").first()

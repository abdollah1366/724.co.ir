"""میز کار انبار با اسکن بارکد: انتقال بین بخش‌ها، مصرف داخلی، ضایعات، انبارگردانی؛ درخواست کالای بخش‌ها و چاپ برچسب."""

import json

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_POST

from apps.audit.documents import DocStatus
from apps.core import barcode as code128
from apps.inventory import barcodes, requests
from apps.inventory import services as inv
from apps.inventory.models import Batch, InventoryDocument, StockBalance, StockRequest, Warehouse
from apps.purchasing.models import GoodsReceipt
from apps.shop.models import Product

Kind = InventoryDocument.Kind
MODES = [
    ("transfer", "انتقال بین انبارها / بخش‌ها", "inventory.post_inventorydocument"),
    ("consume", "مصرف داخلی بخش", "inventory.consume_inventorydocument"),
    ("waste", "ضایعات / امحا", "inventory.post_inventorydocument"),
    ("count", "انبارگردانی (شمارش)", "inventory.count_inventorydocument"),
]
WAREHOUSE_PERMS = ("inventory.view_stockbalance", "inventory.post_inventorydocument", "inventory.consume_inventorydocument",
                   "inventory.add_stockrequest", "inventory.issue_stockrequest")


def _require_any(user, perms):
    if not (user.is_superuser or any(user.has_perm(p) for p in perms)):
        raise PermissionDenied


def _my_warehouses(user):
    """بخش‌هایی که کاربر مسئول آن است؛ انباردار و مدیر (مجوز ثبت سند انبار) همه انبارها را می‌بینند."""
    active = Warehouse.objects.filter(is_active=True)
    return [w for w in active if requests.can_act_for(user, w)]


def _parse_items(raw):
    """فهرست اسکن‌شده از فرم: [{"product": id, "batch": id|null, "quantity": n}]."""
    try:
        rows = json.loads(raw or "[]")
    except ValueError:
        raise ValidationError("فهرست کالاها معتبر نیست.")
    items = []
    products = {p.pk: p for p in Product.objects.filter(pk__in=[r.get("product") for r in rows if isinstance(r, dict)])}
    for row in rows:
        product = products.get(row.get("product"))
        try:
            quantity = int(row.get("quantity") or 0)
        except (TypeError, ValueError):
            quantity = 0
        if product is None or quantity < 0:
            raise ValidationError("ردیف نامعتبر در فهرست کالاها.")
        batch = Batch.objects.filter(pk=row["batch"], product=product).first() if row.get("batch") else None
        items.append({"product": product, "batch": batch, "quantity": quantity})
    if not items:
        raise ValidationError("هیچ کالایی اسکن نشده است.")
    return items


@staff_member_required
def api_scan(request):
    """خواندن یک اسکن برای صفحه‌های انبار: کالا، بچ، انقضا و موجودی همان انبار."""
    _require_any(request.user, WAREHOUSE_PERMS + ("sales.use_pos",))
    scan = barcodes.resolve(request.GET.get("code", ""))
    if not scan.found:
        return JsonResponse({"error": "not_found", "message": "کالایی با این بارکد پیدا نشد."}, status=404)
    data = scan.as_dict()
    data["batch_id"] = scan.batch.pk if scan.batch else None
    warehouse = Warehouse.objects.filter(pk=request.GET.get("warehouse") or 0).first()
    if warehouse:
        qs = StockBalance.objects.filter(product=scan.product, warehouse=warehouse)
        if scan.batch:
            qs = qs.filter(batch=scan.batch)
        data["on_hand"] = sum(b.quantity for b in qs)
    return JsonResponse(data)


@staff_member_required
def workbench(request):
    user = request.user
    modes = [(key, label) for key, label, perm in MODES if user.is_superuser or user.has_perm(perm)]
    if not modes:
        raise PermissionDenied
    warehouses = _my_warehouses(user)
    all_active = Warehouse.objects.filter(is_active=True)
    if request.method == "POST":
        mode = request.POST.get("mode")
        if mode not in dict(modes):
            raise PermissionDenied
        warehouse = get_object_or_404(Warehouse, pk=request.POST.get("warehouse"), is_active=True)
        if warehouse not in warehouses:
            raise PermissionDenied
        reason = request.POST.get("reason", "").strip()
        try:
            items = _parse_items(request.POST.get("items"))
            if mode == "count":
                result = requests.count_adjust(warehouse, [(i["product"], i["batch"], i["quantity"]) for i in items], user, reason)
                changed = [r for r in result["rows"] if r[4]]
                docs = [d.number for d in (result["out"], result["in"]) if d]
                messages.success(request, f"شمارش {len(result['rows'])} ردیف ثبت شد؛ {len(changed)} ردیف اختلاف داشت"
                                          + (f" (سند {'، '.join(docs)})." if docs else "."))
                return render(request, "backoffice/warehouse_count_result.html", {"warehouse": warehouse, "result": result})
            dest = None
            if mode == "transfer":
                dest = get_object_or_404(Warehouse, pk=request.POST.get("dest_warehouse"), is_active=True)
            doc = requests.build_document(mode, warehouse, [i for i in items if i["quantity"] > 0], user,
                                          dest_warehouse=dest, reason=reason)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect(f"{request.path}?mode={mode}&warehouse={warehouse.pk}")
        messages.success(request, f"سند {doc.number} ({doc.get_kind_display()}) با {doc.lines.count()} ردیف ثبت شد.")
        return redirect(f"{request.path}?mode={mode}&warehouse={warehouse.pk}")
    mode = request.GET.get("mode") if request.GET.get("mode") in dict(modes) else modes[0][0]
    current = next((w for w in warehouses if str(w.pk) == request.GET.get("warehouse")), warehouses[0] if warehouses else None)
    recent = InventoryDocument.objects.filter(status=DocStatus.POSTED).select_related("warehouse", "dest_warehouse")
    if not (user.is_superuser or user.has_perm("inventory.post_inventorydocument")):
        recent = recent.filter(Q(warehouse__in=warehouses) | Q(dest_warehouse__in=warehouses))
    return render(request, "backoffice/warehouse.html", {
        "modes": modes, "mode": mode, "warehouses": warehouses, "current": current, "all_warehouses": all_active,
        "recent": recent.order_by("-posted_at")[:10],
    })


# ---------------------------------------------------------------- درخواست کالا از بخش‌ها
@staff_member_required
def stock_requests(request):
    user = request.user
    _require_any(user, ("inventory.add_stockrequest", "inventory.issue_stockrequest"))
    warehouses = _my_warehouses(user)
    can_issue = user.is_superuser or user.has_perm("inventory.issue_stockrequest")
    if request.method == "POST":
        if not user.has_perm("inventory.add_stockrequest"):
            raise PermissionDenied
        requester = get_object_or_404(Warehouse, pk=request.POST.get("requester"), is_active=True)
        if requester not in warehouses:
            raise PermissionDenied
        source = Warehouse.objects.filter(pk=request.POST.get("source") or 0, is_active=True).first()
        try:
            items = _parse_items(request.POST.get("items"))
            req = requests.create_request(requester, [(i["product"], i["quantity"]) for i in items if i["quantity"] > 0],
                                          user, source=source, reason=request.POST.get("reason", ""))
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, f"درخواست {req.number} برای {req.source} فرستاده شد.")
        return redirect("backoffice:stock_requests")
    mine = StockRequest.objects.filter(requester__in=warehouses).select_related("requester", "source", "created_by")
    incoming = StockRequest.objects.none()
    if can_issue:
        incoming = (StockRequest.objects.filter(status=DocStatus.POSTED, progress__in=["open", "partial"])
                    .select_related("requester", "source", "created_by").prefetch_related("lines__product"))
    requesters = [w for w in warehouses if w.kind != Warehouse.Kind.QUARANTINE]
    return render(request, "backoffice/stock_requests.html", {
        "mine": mine.prefetch_related("lines__product")[:30], "incoming": incoming, "can_issue": can_issue,
        "requesters": requesters, "sources": Warehouse.objects.filter(is_active=True).exclude(kind=Warehouse.Kind.QUARANTINE),
        "can_add": user.has_perm("inventory.add_stockrequest"),
    })


@staff_member_required
@require_POST
def stock_request_action(request, pk, action):
    user = request.user
    req = get_object_or_404(StockRequest, pk=pk)
    try:
        if action == "issue":
            _require_any(user, ("inventory.issue_stockrequest",))
            quantities = {}
            for line in req.lines.all():
                value = request.POST.get(f"q{line.pk}")
                if value not in (None, ""):
                    quantities[line.pk] = int(value)
            doc, short = requests.issue_request(req, user, quantities)
            text = f"تحویل با سند انتقال {doc.number} ثبت شد."
            if short:
                text += " کسری: " + "، ".join(f"{p.name} {q}" for p, q in short.items())
            messages.success(request, text)
        elif action == "close":
            _require_any(user, ("inventory.issue_stockrequest",))
            requests.close_request(req, user)
            messages.info(request, f"درخواست {req.number} بسته شد.")
        elif action == "cancel":
            if not (requests.can_act_for(user, req.requester) and user.has_perm("inventory.add_stockrequest")):
                raise PermissionDenied
            req.reverse(user, request.POST.get("reason") or "انصراف بخش")
            messages.info(request, f"درخواست {req.number} لغو شد.")
        else:
            raise PermissionDenied
    except (ValidationError, ValueError) as exc:
        messages.error(request, " ".join(getattr(exc, "messages", [str(exc)])))
    return redirect("backoffice:stock_requests")


# ---------------------------------------------------------------- برچسب بارکد
LABEL_PERMS = ("inventory.post_inventorydocument", "shop.change_product", "purchasing.post_goodsreceipt")


@staff_member_required
def labels(request):
    _require_any(request.user, LABEL_PERMS)
    if request.method == "POST":
        product = get_object_or_404(Product, pk=request.POST.get("product"))
        code = barcodes.ensure_barcode(product)
        messages.success(request, f"بارکد «{product.name}»: {code}")
        return redirect(f"{request.path}?q={product.sku or product.name}")
    q = request.GET.get("q", "").strip()
    copies = max(1, min(int(request.GET.get("copies") or 1), 200)) if (request.GET.get("copies") or "1").isdigit() else 1
    labels_, products = [], []
    receipt = None
    if request.GET.get("receipt"):
        receipt = get_object_or_404(GoodsReceipt, pk=request.GET["receipt"])
        for line in receipt.lines.select_related("product"):
            batch = Batch.objects.filter(product=line.product, number=Batch.GENERIC_NUMBER if not line.product.track_batch
                                         else line.batch_number.strip()).first()
            labels_ += [_label(line.product, batch)] * (line.quantity if request.GET.get("per_unit") else 1)
    elif q:
        scan = barcodes.resolve(q)
        if scan.found:
            products = [scan.product]
            if scan.batch:
                labels_ = [_label(scan.product, scan.batch)] * copies
        else:
            products = list(Product.objects.filter(Q(name__icontains=q) | Q(sku__iexact=q) | Q(generic_name__icontains=q))[:20])
        if products and not labels_ and request.GET.get("product"):
            product = next((p for p in products if str(p.pk) == request.GET["product"]), None)
            batch = Batch.objects.filter(pk=request.GET.get("batch") or 0, product=product).first() if product else None
            if product:
                labels_ = [_label(product, batch)] * copies
    return render(request, "backoffice/labels.html", {
        "q": q, "copies": copies, "products": [_product_row(p) for p in products], "labels": labels_, "receipt": receipt,
        "recent_receipts": GoodsReceipt.objects.filter(status=DocStatus.POSTED).select_related("supplier").order_by("-posted_at")[:8],
    })


def _product_row(product):
    package = product.packages.exclude(barcode__isnull=True).exclude(barcode="").order_by("factor").first()
    batches = list(Batch.objects.filter(product=product, balances__quantity__gt=0).distinct().order_by("expiry_date"))
    return {"product": product, "barcode": package.barcode if package else "", "batches": batches}


def _label(product, batch=None):
    """یک برچسب: بارکد بچ (اگر بچ دارد، برای انبار) یا بارکد کالا (برای قفسه و صندوق)."""
    if batch is not None:
        code = barcodes.batch_label(batch)
    else:
        code = barcodes.ensure_barcode(product)
    return {
        "name": product.name, "price": product.price, "code": code, "svg": mark_safe(code128.svg(code, height=50, module=2)),
        "batch": batch.number if batch and batch.number != Batch.GENERIC_NUMBER else "",
        "expiry": batch.expiry_date if batch else None, "storage": product.get_storage_condition_display(),
    }

"""خرید و ورود کالا در پیشخوان: یک فرم برای تحویل بار شرکت پخش و یک صفحه برای کارهای مانده (فاکتور، پرداخت، سفارش).

جای چهار سند جدا در پنل مدیریت (سفارش ← رسید ← فاکتور ← پرداخت) را می‌گیرد؛ اسناد و کنترل‌ها همان قبلی‌اند.
"""

import json

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.accounting.models import Party, PaymentMethod
from apps.audit.documents import DocStatus
from apps.core.validators import normalize_digits
from apps.inventory import barcodes
from apps.inventory.models import Location, Warehouse
from apps.purchasing import quick
from apps.purchasing.models import GoodsReceipt, PurchaseOrder
from apps.shop.models import Product

RECEIVE_PERM = "purchasing.post_goodsreceipt"
INVOICE_PERM = "purchasing.post_purchaseinvoice"
PAY_PERM = "accounting.post_payment"
ORDER_PERM = "purchasing.add_purchaseorder"
HUB_PERMS = (RECEIVE_PERM, INVOICE_PERM, PAY_PERM, ORDER_PERM)


def _require(user, *perms):
    if not (user.is_superuser or any(user.has_perm(p) for p in perms)):
        raise PermissionDenied


def _int(value, default=0):
    try:
        return int(normalize_digits(str(value or "")).replace(",", "").replace("٬", "").strip() or default)
    except ValueError:
        raise ValidationError(f"عدد «{value}» معتبر نیست.")


def product_info(product, scan=None):
    """اطلاعات لازم برای یک ردیف ورود کالا (برای جست‌وجو و بازسازی فرم پس از خطا)."""
    data = {
        "id": product.pk, "name": product.name, "sku": product.sku or "", "price": product.price,
        "cond": product.storage_condition, "cond_label": product.get_storage_condition_display(),
        "track_batch": product.track_batch, "track_expiry": product.track_expiry, "track_serial": product.track_serial,
        "auth": product.requires_authenticity_check, "last_cost": quick.last_cost(product), "factor": 1,
    }
    if scan is not None:
        data.update({"factor": scan.factor or 1, "batch": scan.batch.number if scan.batch else scan.batch_number,
                     "expiry": scan.expiry.isoformat() if scan.expiry else "", "serial": scan.serial})
        if scan.batch and not data["expiry"] and scan.batch.expiry_date:
            data["expiry"] = scan.batch.expiry_date.isoformat()
    return data


@staff_member_required
def api_lookup(request):
    """بارکد / کد دوبعدی / کد کالا ← یک کالا؛ در غیر این صورت جست‌وجوی نام (حداکثر ۱۰ مورد)."""
    _require(request.user, RECEIVE_PERM, ORDER_PERM)
    text = request.GET.get("q", "").strip()[:200]
    if not text:
        return JsonResponse({"results": []})
    scan = barcodes.resolve(text)
    if scan.found:
        return JsonResponse({"results": [product_info(scan.product, scan)]})
    found = Product.objects.filter(is_active=True).filter(
        Q(name__icontains=text) | Q(generic_name__icontains=text) | Q(irc__iexact=text) | Q(brand__icontains=text)
    ).order_by("name")[:10]
    return JsonResponse({"results": [product_info(p) for p in found]})


def _receiving_warehouses():
    return Warehouse.objects.filter(is_active=True).exclude(kind=Warehouse.Kind.QUARANTINE)


def _parse_rows(raw, warehouse):
    try:
        data = json.loads(raw or "[]")
    except ValueError:
        raise ValidationError("فهرست کالاها معتبر نیست.")
    products = Product.objects.in_bulk([r.get("product") for r in data if isinstance(r, dict)])
    locations = Location.objects.in_bulk([r.get("location") for r in data if isinstance(r, dict) and r.get("location")])
    rows = []
    for number, item in enumerate(data, 1):
        product = products.get(item.get("product"))
        if product is None:
            raise ValidationError(f"ردیف {number}: کالا پیدا نشد.")
        quantity, cost = _int(item.get("quantity")), _int(item.get("unit_cost"))
        if quantity <= 0:
            raise ValidationError(f"ردیف {number} («{product.name}»): تعداد را وارد کنید.")
        location = locations.get(item.get("location")) if item.get("location") else None
        if location is not None and location.warehouse_id != warehouse.pk:
            location = None
        rows.append({
            "product": product, "quantity": quantity, "unit_cost": cost, "location": location,
            "batch_number": str(item.get("batch") or ""), "expiry_date": quick.parse_expiry(item.get("expiry")),
            "authenticity_code": str(item.get("auth_code") or ""), "authenticity_verified": bool(item.get("auth_ok")),
        })
    return rows


def _restore(raw):
    """ردیف‌های ارسال‌شده را با اطلاعات کالا برمی‌گرداند تا پس از خطا چیزی از فرم پاک نشود."""
    try:
        data = [r for r in json.loads(raw or "[]") if isinstance(r, dict)]
    except ValueError:
        return []
    products = Product.objects.in_bulk([r.get("product") for r in data])
    restored = []
    for item in data:
        product = products.get(item.get("product"))
        if product:
            restored.append({**item, "info": product_info(product)})
    return restored


@staff_member_required
def receive(request):
    """ورود بار شرکت پخش در یک صفحه: کالا را اسکن یا جست‌وجو کنید، بچ و انقضا و تعداد و قیمت را بنویسید، ثبت."""
    user = request.user
    _require(user, RECEIVE_PERM)
    can_invoice = user.is_superuser or user.has_perm(INVOICE_PERM)
    can_pay = user.is_superuser or user.has_perm(PAY_PERM)
    can_add_supplier = user.is_superuser or user.has_perm("accounting.add_party")
    warehouses = _receiving_warehouses()
    suppliers = Party.objects.filter(kind=Party.Kind.SUPPLIER, is_active=True).order_by("name")
    po = None
    if request.GET.get("po") or request.POST.get("po"):
        po = get_object_or_404(PurchaseOrder, pk=request.GET.get("po") or request.POST.get("po"), status=DocStatus.POSTED)
    form = request.POST if request.method == "POST" else {}
    initial_rows = []
    if po is not None and request.method != "POST":
        for line in po.lines.select_related("product"):
            left = line.quantity - line.received_quantity
            if left > 0:
                initial_rows.append({"product": line.product_id, "quantity": left, "unit_cost": line.unit_price,
                                     "info": product_info(line.product)})
    if request.method == "POST":
        initial_rows = _restore(request.POST.get("items"))
        try:
            with transaction.atomic():  # شرکت تازه هم فقط با ثبت موفق رسید ساخته می‌شود
                warehouse = get_object_or_404(warehouses, pk=request.POST.get("warehouse"))
                supplier = _supplier_from(request.POST, suppliers, can_add_supplier)
                if po is not None and po.supplier_id != supplier.pk:
                    raise ValidationError("تأمین‌کننده با سفارش خرید یکی نیست.")
                doc_no = request.POST.get("doc_no", "").strip()
                rows = _parse_rows(request.POST.get("items"), warehouse)
                invoice = payment = None
                if can_invoice and request.POST.get("with_invoice"):
                    if not doc_no:
                        raise ValidationError("برای ثبت فاکتور، شماره فاکتور شرکت را بنویسید.")
                    invoice = {"supplier_invoice_no": doc_no, "discount": _int(request.POST.get("discount")),
                               "tax": _int(request.POST.get("tax"))}
                if can_pay and invoice and _int(request.POST.get("pay_amount")):
                    payment = {"method": request.POST.get("pay_method") or PaymentMethod.BANK,
                               "amount": _int(request.POST.get("pay_amount")), "reference_no": request.POST.get("pay_ref", "")}
                    if payment["method"] not in PaymentMethod.values:
                        raise ValidationError("روش پرداخت معتبر نیست.")
                receipt, bill, paid = quick.quick_receive(
                    supplier=supplier, warehouse=warehouse, rows=rows, user=user, delivery_note=doc_no,
                    purchase_order=po, invoice=invoice, payment=payment,
                )
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        except IntegrityError:
            messages.error(request, "این شماره فاکتور برای همین شرکت قبلاً ثبت شده یا شماره پیگیری پرداخت تکراری است.")
        else:
            text = f"رسید انبار {receipt.number} با {receipt.lines.count():,} ردیف ثبت شد و کالا وارد «{receipt.warehouse}» شد."
            if bill:
                text += f" فاکتور خرید {bill.number} به مبلغ {bill.total:,} ریال ثبت شد."
            else:
                text += " فاکتور خرید در «خرید و بدهی شرکت‌ها» منتظر ثبت است."
            if paid:
                text += f" پرداخت {paid.number} ثبت شد."
            messages.success(request, text)
            return redirect(f"{reverse('backoffice:purchases')}?done={receipt.pk}")
    default_wh = next((w for w in warehouses if w.code == "MAIN"), warehouses.first())
    locations = [{"id": loc.pk, "wh": loc.warehouse_id, "cond": loc.storage_condition, "name": loc.name}
                 for loc in Location.objects.filter(is_active=True, warehouse__in=warehouses).order_by("code")]
    return render(request, "backoffice/receive.html", {
        "warehouses": warehouses, "suppliers": suppliers, "po": po, "form": form,
        "current_wh": str(form.get("warehouse") or (default_wh.pk if default_wh else "")),
        "current_supplier": str(form.get("supplier") or (po.supplier_id if po else "")),
        "can_invoice": can_invoice, "can_pay": can_pay, "can_add_supplier": can_add_supplier,
        "pay_methods": PaymentMethod.choices, "rows_json": initial_rows, "locations_json": locations,
        "with_invoice": (form.get("with_invoice") if request.method == "POST" else True),
    })


def _supplier_from(post, suppliers, can_add):
    new_name = post.get("new_supplier", "").strip()
    if new_name:
        if not can_add:
            raise PermissionDenied
        existing = suppliers.filter(name=new_name).first()
        if existing:
            return existing
        return Party.objects.create(kind=Party.Kind.SUPPLIER, name=new_name[:200],
                                    license_no=post.get("new_supplier_license", "").strip()[:60],
                                    phone=post.get("new_supplier_phone", "").strip()[:20])
    supplier = suppliers.filter(pk=post.get("supplier") or 0).first()
    if supplier is None:
        raise ValidationError("شرکت پخش (تأمین‌کننده) را انتخاب کنید.")
    return supplier


@staff_member_required
def purchases(request):
    """خرید و بدهی شرکت‌ها: رسیدهای منتظر فاکتور، بدهی هر شرکت و پرداخت، سفارش‌های در راه."""
    user = request.user
    _require(user, *HUB_PERMS)
    can_invoice = user.is_superuser or user.has_perm(INVOICE_PERM)
    can_pay = user.is_superuser or user.has_perm(PAY_PERM)
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "invoice":
                _require(user, INVOICE_PERM)
                receipt = get_object_or_404(quick.receipts_without_invoice(), pk=request.POST.get("receipt"))
                bill = quick.invoice_receipt(receipt, user, request.POST.get("supplier_invoice_no", ""),
                                             _int(request.POST.get("discount")), _int(request.POST.get("tax")))
                messages.success(request, f"فاکتور خرید {bill.number} برای رسید {receipt.number} به مبلغ {bill.total:,} ریال ثبت شد.")
            elif action == "pay":
                _require(user, PAY_PERM)
                supplier = get_object_or_404(Party, pk=request.POST.get("supplier"), kind=Party.Kind.SUPPLIER)
                method = request.POST.get("method")
                if method not in PaymentMethod.values:
                    raise ValidationError("روش پرداخت معتبر نیست.")
                paid = quick.pay_supplier(supplier, user, method, _int(request.POST.get("amount")),
                                          request.POST.get("reference_no", ""))
                messages.success(request, f"پرداخت {paid.number} به «{supplier}» به مبلغ {paid.amount:,} ریال ثبت شد.")
            else:
                raise PermissionDenied
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        except IntegrityError:
            messages.error(request, "شماره فاکتور یا شماره پیگیری تکراری است.")
        return redirect("backoffice:purchases")
    done = GoodsReceipt.objects.filter(pk=request.GET.get("done") or 0, status=DocStatus.POSTED).first()
    return render(request, "backoffice/purchases.html", {
        "pending": quick.receipts_without_invoice(), "balances": quick.supplier_balances() if (can_pay or can_invoice) else [],
        "orders": quick.open_orders(), "can_invoice": can_invoice, "can_pay": can_pay,
        "can_receive": user.is_superuser or user.has_perm(RECEIVE_PERM), "can_order": user.is_superuser or user.has_perm(ORDER_PERM),
        "pay_methods": PaymentMethod.choices, "done": done,
        "recent": GoodsReceipt.objects.filter(status=DocStatus.POSTED).select_related("supplier", "warehouse").order_by("-posted_at")[:10],
    })

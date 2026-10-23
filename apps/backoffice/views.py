"""پیشخوان کارکنان: داشبورد لحظه‌ای، هشدارها، گزارش‌ها، صندوق فروش و API داخلی."""

import datetime
import uuid

import jdatetime
from django import forms
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounting import services as acc
from apps.accounting.models import Party
from apps.audit.documents import DocStatus
from apps.core.validators import is_valid_national_code, normalize_digits
from apps.ehr import services as ehr
from apps.inventory import alerts
from apps.inventory import services as inv
from apps.inventory.models import StockBalance, StockMove, Warehouse
from apps.purchasing.models import GoodsReceipt
from apps.sales import services as sales
from apps.sales.models import CashRegister, CashSession, SalesInvoice, SalesPayment
from apps.shop.models import Order, Product, ProductPackage

FINANCE_PERM = "accounting.view_financial_reports"


def _month_start(today):
    j = jdatetime.date.fromgregorian(date=today)
    return jdatetime.date(j.year, j.month, 1).togregorian()


def _date_param(request, name, default):
    value = normalize_digits(request.GET.get(name, ""))
    try:
        return datetime.date.fromisoformat(value) if value else default
    except ValueError:
        return default


def dashboard_data(user):
    today = timezone.localdate()
    data = {"today": today, "alert_counts": alerts.counts()}
    posted_today = SalesInvoice.objects.filter(status=DocStatus.POSTED, date=today)
    data["sales_today_count"] = posted_today.count()
    data["open_orders"] = Order.objects.filter(status__in=inv.OPEN_ORDER_STATUSES).count()
    data["open_sessions"] = CashSession.objects.filter(status=CashSession.Status.OPEN).select_related("register", "cashier")
    if user.has_perm(FINANCE_PERM):
        day = acc.profit_and_loss(today, today)
        month = acc.profit_and_loss(_month_start(today), today)
        data["finance"] = {
            "sales_today": day["total_revenue"],
            "gross_profit_today": day["gross_profit"],
            "sales_month": month["total_revenue"],
            "gross_profit_month": month["gross_profit"],
            "net_profit_month": month["net_profit"],
            "inventory_value": acc.account_balance(acc.INVENTORY),
            "inventory_ledger_value": StockMove.objects.aggregate(v=Sum("total_cost"))["v"] or 0,
            "cash": acc.account_balance(acc.CASH),
            "bank": acc.account_balance(acc.BANK),
            "receivable": acc.account_balance(acc.AR_CUSTOMERS) + acc.account_balance(acc.AR_INSURERS),
            "payable": -acc.account_balance(acc.AP_SUPPLIERS) - acc.account_balance(acc.AP_OTHER),
        }
    return data


@staff_member_required
def dashboard(request):
    context = dashboard_data(request.user)
    context["recent_sales"] = SalesInvoice.objects.filter(status=DocStatus.POSTED).order_by("-posted_at")[:8]
    context["recent_receipts"] = GoodsReceipt.objects.filter(status=DocStatus.POSTED).select_related("supplier").order_by("-posted_at")[:5]
    context["top_products"] = (
        StockMove.objects.filter(move_type=StockMove.Type.SALE, created_at__date__gte=_month_start(context["today"]))
        .values("product__name").annotate(qty=-Sum("quantity")).order_by("-qty")[:5]
    )
    return render(request, "backoffice/dashboard.html", context)


@staff_member_required
def alerts_view(request):
    return render(request, "backoffice/alerts.html", {"alerts": alerts.all_alerts()})


@staff_member_required
def stock_report(request):
    balances = StockBalance.objects.filter(quantity__gt=0).select_related("product", "warehouse", "location", "batch")
    q = request.GET.get("q", "").strip()[:100]
    if q:
        balances = balances.filter(Q(product__name__icontains=q) | Q(product__sku=q) | Q(batch__number__icontains=q))
    warehouse = request.GET.get("warehouse")
    if warehouse:
        balances = balances.filter(warehouse__code=warehouse)
    balances = balances.order_by("product__name", "batch__expiry_date")
    return render(request, "backoffice/stock.html", {
        "balances": balances, "q": q, "warehouses": Warehouse.objects.all(), "current_warehouse": warehouse,
    })


@staff_member_required
def kardex(request, pk):
    product = get_object_or_404(Product, pk=pk)
    moves = StockMove.objects.filter(product=product).select_related("warehouse", "batch", "user").order_by("id")
    return render(request, "backoffice/kardex.html", {"product": product, "moves": moves,
                                                       "price_history": product.price_history.all()[:20]})


@staff_member_required
@permission_required(FINANCE_PERM, raise_exception=True)
def profit_loss(request):
    today = timezone.localdate()
    start = _date_param(request, "start", _month_start(today))
    end = _date_param(request, "end", today)
    return render(request, "backoffice/pnl.html", {"report": acc.profit_and_loss(start, end), "start": start, "end": end})


@staff_member_required
@permission_required(FINANCE_PERM, raise_exception=True)
def trial_balance(request):
    rows, totals = acc.trial_balance()
    return render(request, "backoffice/trial_balance.html", {"rows": rows, "totals": totals})


@staff_member_required
@permission_required(FINANCE_PERM, raise_exception=True)
def parties(request):
    kind = request.GET.get("kind") or None
    return render(request, "backoffice/parties.html", {"rows": acc.party_balances(kind), "kinds": Party.Kind.choices, "kind": kind})


@staff_member_required
@permission_required(FINANCE_PERM, raise_exception=True)
def party_statement(request, pk):
    party = get_object_or_404(Party, pk=pk)
    return render(request, "backoffice/party_statement.html", {"party": party, "rows": acc.party_statement(party)})


# --- صندوق فروش حضوری ---
class OpenSessionForm(forms.Form):
    register = forms.ModelChoiceField(label="صندوق", queryset=CashRegister.objects.filter(is_active=True))
    opening_cash = forms.IntegerField(label="موجودی اولیه نقد (ریال)", min_value=0, initial=0)


class CloseSessionForm(forms.Form):
    counted_cash = forms.IntegerField(label="نقد شمارش‌شده (ریال)", min_value=0)


class CheckoutForm(forms.Form):
    token = forms.CharField(widget=forms.HiddenInput)
    discount = forms.IntegerField(label="تخفیف فاکتور (ریال)", min_value=0, initial=0)
    cash = forms.IntegerField(label="نقد (ریال)", min_value=0, initial=0)
    card = forms.IntegerField(label="کارتخوان (ریال)", min_value=0, initial=0)
    card_ref = forms.CharField(label="شماره پیگیری کارتخوان", max_length=40, required=False)
    credit = forms.IntegerField(label="نسیه (ریال)", min_value=0, initial=0)
    customer = forms.ModelChoiceField(label="مشتری (برای نسیه)", queryset=Party.objects.filter(kind=Party.Kind.CUSTOMER, is_active=True), required=False)
    insurer = forms.ModelChoiceField(label="بیمه‌گر", queryset=Party.objects.filter(kind=Party.Kind.INSURER, is_active=True), required=False)
    insurance_amount = forms.IntegerField(label="سهم بیمه (ریال)", min_value=0, initial=0)
    prescription_number = forms.CharField(label="شماره / کد رهگیری نسخه", max_length=40, required=False)
    prescriber_name = forms.CharField(label="نام پزشک", max_length=120, required=False)
    prescriber_council_no = forms.CharField(label="نظام پزشکی", max_length=20, required=False)
    patient_national_code = forms.CharField(
        label="کد ملی بیمار (ثبت در پرونده سلامت)", max_length=10, required=False,
        help_text="دارو یا تجهیزات تحویلی در پرونده بیمار دیده می‌شود.",
    )

    def clean_patient_national_code(self):
        value = normalize_digits(self.cleaned_data.get("patient_national_code") or "")
        if value and not is_valid_national_code(value):
            raise forms.ValidationError("کد ملی بیمار معتبر نیست.")
        return value


def _open_session(user):
    return CashSession.objects.filter(cashier=user, status=CashSession.Status.OPEN).select_related("register").first()


def _current_invoice(session, user):
    return SalesInvoice.objects.filter(session=session, created_by=user, status=DocStatus.DRAFT).order_by("-id").first()


def find_product(code):
    code = normalize_digits(code)
    if not code:
        return None, 1
    package = ProductPackage.objects.select_related("product").filter(barcode=code).first()
    if package:
        return package.product, package.factor
    product = Product.objects.filter(Q(sku=code) | Q(slug=code)).first() or Product.objects.filter(name=code).first()
    return product, 1


pos_required = permission_required("sales.use_pos", raise_exception=True)


@staff_member_required
@pos_required
def pos(request):
    session = _open_session(request.user)
    if session is None:
        form = OpenSessionForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            try:
                sales.open_session(form.cleaned_data["register"], request.user, form.cleaned_data["opening_cash"])
            except (ValidationError, IntegrityError):
                messages.error(request, "این صندوق شیفت باز دارد.")
            return redirect("backoffice:pos")
        return render(request, "backoffice/pos_open.html", {"form": form})
    invoice = _current_invoice(session, request.user)
    checkout = None
    if invoice:
        checkout = CheckoutForm(initial={"token": invoice.client_token or "", "cash": invoice.total})
    return render(request, "backoffice/pos.html", {
        "session": session, "invoice": invoice, "checkout": checkout,
        "lines": invoice.lines.select_related("product") if invoice else [],
        "expected_cash": sales.session_expected_cash(session), "close_form": CloseSessionForm(),
    })


@staff_member_required
@pos_required
@require_POST
def pos_add(request):
    session = _open_session(request.user)
    if session is None:
        return redirect("backoffice:pos")
    product, factor = find_product(request.POST.get("code", ""))
    try:
        quantity = max(1, int(normalize_digits(request.POST.get("quantity", "1")) or 1)) * factor
    except ValueError:
        quantity = factor
    if product is None:
        messages.error(request, "کالایی با این بارکد یا کد پیدا نشد.")
        return redirect("backoffice:pos")
    invoice = _current_invoice(session, request.user) or sales.create_pos_invoice(session, request.user, uuid.uuid4().hex)
    try:
        sales.add_line(invoice, product, quantity)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect("backoffice:pos")


@staff_member_required
@pos_required
@require_POST
def pos_remove(request, pk):
    session = _open_session(request.user)
    invoice = _current_invoice(session, request.user) if session else None
    if invoice:
        invoice.lines.filter(pk=pk).delete()
    return redirect("backoffice:pos")


@staff_member_required
@pos_required
@require_POST
def pos_checkout(request):
    form = CheckoutForm(request.POST)
    if not form.is_valid():
        messages.error(request, "اطلاعات پرداخت معتبر نیست.")
        return redirect("backoffice:pos")
    data = form.cleaned_data
    # جلوگیری از ثبت تکراری: اگر این فرم قبلاً ثبت شده، همان فاکتور نمایش داده می‌شود.
    existing = SalesInvoice.objects.filter(client_token=data["token"], created_by=request.user).first()
    if existing is None:
        return redirect("backoffice:pos")
    if existing.status != DocStatus.DRAFT:
        return redirect("backoffice:pos_invoice", pk=existing.pk)
    invoice = existing
    try:
        with transaction.atomic():
            invoice.discount = data["discount"] or 0
            invoice.insurance_amount = data["insurance_amount"] or 0
            invoice.prescription_number = data["prescription_number"]
            invoice.prescriber_name = data["prescriber_name"]
            invoice.prescriber_council_no = data["prescriber_council_no"]
            invoice.insurer = data["insurer"]
            invoice.customer = data["customer"]
            if data["patient_national_code"]:
                invoice.patient = ehr.get_or_create_patient(data["patient_national_code"])
            invoice.save()
            invoice.payments.all().delete()
            for method, amount, ref in (
                (SalesPayment.Method.CASH, data["cash"], ""),
                (SalesPayment.Method.CARD, data["card"], data["card_ref"]),
                (SalesPayment.Method.CREDIT, data["credit"], ""),
            ):
                if amount:
                    SalesPayment.objects.create(document=invoice, method=method, amount=amount, reference_no=ref)
            invoice.post(request.user)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("backoffice:pos")
    messages.success(request, f"فاکتور {invoice.number} ثبت شد.")
    return redirect("backoffice:pos_invoice", pk=invoice.pk)


@staff_member_required
@pos_required
@require_POST
def pos_close(request):
    session = _open_session(request.user)
    form = CloseSessionForm(request.POST)
    if session and form.is_valid():
        try:
            session = sales.close_session(session, form.cleaned_data["counted_cash"], request.user)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect("backoffice:pos")
        messages.success(request, f"شیفت بسته شد. اختلاف صندوق: {session.difference} ریال")
    return redirect("backoffice:pos")


@staff_member_required
def pos_invoice(request, pk):
    invoice = get_object_or_404(SalesInvoice.objects.select_related("customer", "insurer", "posted_by"), pk=pk)
    if not (request.user.has_perm("sales.view_salesinvoice") or invoice.created_by_id == request.user.pk):
        raise PermissionDenied
    return render(request, "backoffice/invoice.html", {
        "invoice": invoice, "lines": invoice.lines.select_related("product").prefetch_related("allocations__batch"),
    })


# --- API داخلی (JSON، فقط کارکنان) ---
def _product_json(product, factor=1):
    return {
        "id": product.pk, "name": product.name, "sku": product.sku, "kind": product.kind, "price": product.price,
        "factor": factor, "available": product.stock, "on_hand": inv.on_hand(product),
        "requires_prescription": product.requires_prescription, "is_controlled": product.is_controlled,
        "is_sellable": product.is_sellable, "storage_condition": product.storage_condition,
    }


@staff_member_required
def api_product_lookup(request):
    product, factor = find_product(request.GET.get("code", ""))
    if product is None:
        return JsonResponse({"error": "not_found"}, status=404)
    return JsonResponse(_product_json(product, factor))


@staff_member_required
def api_product_stock(request, pk):
    product = get_object_or_404(Product, pk=pk)
    balances = StockBalance.objects.filter(product=product, quantity__gt=0).select_related("warehouse", "location", "batch")
    return JsonResponse({
        **_product_json(product),
        "avg_cost": str(product.avg_cost),
        "balances": [
            {"warehouse": b.warehouse.code, "location": b.location.code, "batch": b.batch.number,
             "expiry": b.batch.expiry_date.isoformat() if b.batch.expiry_date else None,
             "status": b.batch.status, "quantity": b.quantity}
            for b in balances
        ],
    })


@staff_member_required
def api_dashboard(request):
    data = dashboard_data(request.user)
    data.pop("open_sessions")
    data["today"] = data["today"].isoformat()
    return JsonResponse(data)

"""صفحات مالی: داشبورد، صندوق درمانگاه، صورتحساب‌ها و مطالبات، گزارش‌ها، مغایرت‌گیری، وضعیت اتصال‌ها و تنظیمات،
و صفحات «سهم من» برای پزشک و «فیش حقوق» برای کارمند.

همه کنترل‌ها سمت سرور است: مجوز Django برای کارکنان و مالکیت (پزشک فقط قرارداد خودش، کارمند فقط فیش خودش).
"""

import datetime
import uuid

from django import forms
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounting import services as acc
from apps.accounting.models import Account, BankReconciliation, CostCenter
from apps.audit.documents import DocStatus
from apps.core import events, flags
from apps.core.models import OutboxEvent
from apps.core.validators import normalize_digits

from . import adapters, reconcile
from .models import Contract, Invoice, PayrollLine, ProviderSettlement, Receipt, Refund, TenderMethod
from .services import billing, payroll, settlements

FINANCE_PERM = "accounting.view_financial_reports"
CASHIER_PERM = "finance.use_cashier"


def _require(user, *perms):
    if not user.is_superuser and not any(user.has_perm(p) for p in perms):
        raise PermissionDenied


def _feature(key):
    if not flags.is_enabled(key):
        raise Http404("این بخش در تنظیمات ماژول‌ها خاموش است.")


def _date_param(request, name, default):
    value = normalize_digits(request.GET.get(name, ""))
    try:
        return datetime.date.fromisoformat(value) if value else default
    except ValueError:
        return default


def _month_start(today):
    import jdatetime

    j = jdatetime.date.fromgregorian(date=today)
    return jdatetime.date(j.year, j.month, 1).togregorian()


# ---------------------------------------------------------------- داشبورد
@staff_member_required
def dashboard(request):
    _feature("finance")
    _require(request.user, FINANCE_PERM, CASHIER_PERM)
    today = timezone.localdate()
    context = {"today": today, "can_report": request.user.has_perm(FINANCE_PERM) or request.user.is_superuser}
    receipts_today = Receipt.objects.filter(status=DocStatus.POSTED, date=today)
    context["receipts_today"] = sum(r.total for r in receipts_today.prefetch_related("tenders"))
    context["receipts_count"] = receipts_today.count()
    context["draft_invoices"] = Invoice.objects.filter(status=DocStatus.DRAFT).count()
    context["refunds_waiting"] = Refund.objects.filter(status=DocStatus.DRAFT).count()
    if context["can_report"]:
        month = _month_start(today)
        context["centers"] = acc.profit_and_loss_by_center(month, today)
        context["ar_patients"] = acc.account_balance(acc.AR_PATIENTS)
        context["advances"] = -acc.account_balance(acc.PATIENT_ADVANCES)
        context["providers_payable"] = -acc.account_balance(acc.AP_PROVIDERS)
        context["cash"] = sum(acc.account_balance(a.code) for a in Account.objects.filter(is_cash=True))
        context["events"] = events.health()
        context["settlements_waiting"] = ProviderSettlement.objects.filter(status=DocStatus.DRAFT).count()
    return render(request, "finance/dashboard.html", context)


# ---------------------------------------------------------------- صندوق درمانگاه
class TenderForm(forms.Form):
    cash = forms.IntegerField(label="نقد (ریال)", min_value=0, initial=0)
    card = forms.IntegerField(label="کارتخوان (ریال)", min_value=0, initial=0)
    card_ref = forms.CharField(label="شماره پیگیری کارتخوان", max_length=64, required=False)
    bank = forms.IntegerField(label="انتقال بانکی (ریال)", min_value=0, initial=0)
    bank_ref = forms.CharField(label="شماره پیگیری انتقال", max_length=64, required=False)
    advance = forms.IntegerField(label="از علی‌الحساب بیمار (ریال)", min_value=0, initial=0)
    token = forms.CharField(widget=forms.HiddenInput)

    def tenders(self):
        d = self.cleaned_data
        return [t for t in (
            {"method": TenderMethod.CASH, "amount": d["cash"]},
            {"method": TenderMethod.CARD, "amount": d["card"], "reference_no": d["card_ref"]},
            {"method": TenderMethod.BANK, "amount": d["bank"], "reference_no": d["bank_ref"]},
            {"method": TenderMethod.ADVANCE, "amount": d["advance"]},
        ) if t["amount"]]


class InvoiceForm(forms.Form):
    doctor = forms.TypedChoiceField(label="پزشک / ارائه‌دهنده", coerce=int, required=False)
    service = forms.TypedChoiceField(label="خدمت (از تعرفه درمانگاه)", coerce=int, required=False)
    visit = forms.BooleanField(label="هزینه ویزیت پزشک", required=False)
    quantity = forms.IntegerField(label="تعداد", min_value=1, initial=1)
    discount = forms.IntegerField(label="تخفیف (ریال)", min_value=0, initial=0)
    discount_reason = forms.CharField(label="علت تخفیف", max_length=200, required=False)
    token = forms.CharField(widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.doctors = {pk: (name, fee) for pk, name, fee in adapters.doctors()}
        self.services = {pk: (title, price, code) for pk, title, price, code in adapters.priced_services()}
        self.fields["doctor"].choices = [("", "—")] + [(pk, name) for pk, (name, _) in self.doctors.items()]
        self.fields["service"].choices = [("", "—")] + [(pk, f"{t} ({p:,} ریال)") for pk, (t, p, _) in self.services.items()]

    def clean(self):
        data = super().clean()
        doctor, service, visit = data.get("doctor"), data.get("service"), data.get("visit")
        lines = []
        if visit:
            if not doctor or not self.doctors[doctor][1]:
                raise forms.ValidationError("برای هزینه ویزیت، پزشکی با هزینه ویزیت ثبت‌شده انتخاب کنید.")
            lines.append({"description": "ویزیت", "service_code": "VISIT", "unit_price": self.doctors[doctor][1]})
        if service:
            title, price, code = self.services[service]
            lines.append({"description": title, "service_code": code, "unit_price": price, "quantity": data.get("quantity") or 1})
        if not lines:
            raise forms.ValidationError("دست‌کم یک خدمت یا هزینه ویزیت انتخاب کنید.")
        # تخفیف به ترتیب روی ردیف‌ها پخش می‌شود و از مبلغ آن‌ها بیشتر نمی‌شود (مبالغ فقط از تعرفه سمت سرور).
        remaining = data.get("discount") or 0
        for line in lines:
            gross = line["unit_price"] * line.get("quantity", 1)
            line["discount"] = min(remaining, gross)
            remaining -= line["discount"]
        if remaining:
            raise forms.ValidationError("تخفیف بیشتر از مبلغ خدمات است.")
        data["lines"] = lines
        return data


class RefundForm(forms.Form):
    invoice = forms.TypedChoiceField(label="از صورتحساب", coerce=int, required=False)
    amount = forms.IntegerField(label="مبلغ (ریال)", min_value=1)
    method = forms.ChoiceField(label="روش", choices=[c for c in TenderMethod.choices if c[0] not in ("advance", "online")])
    reference_no = forms.CharField(label="شماره پیگیری", max_length=64, required=False)
    reason = forms.CharField(label="علت", max_length=300)
    token = forms.CharField(widget=forms.HiddenInput)


def _patient_from_request(request):
    patient_id = request.GET.get("patient") or request.POST.get("patient")
    if patient_id:
        return adapters.get_patient(patient_id)
    query = request.GET.get("q", "")
    return adapters.find_patient(query) if query else None


@staff_member_required
def cashier(request):
    _feature("finance.billing")
    _require(request.user, CASHIER_PERM)
    patient = _patient_from_request(request)
    context = {"q": request.GET.get("q", ""), "patient": patient}
    if request.GET.get("q") and not patient:
        messages.error(request, "بیماری با این کد ملی یا شماره پرونده پیدا نشد.")
    if patient:
        token = uuid.uuid4().hex
        context.update({
            "summary": billing.patient_summary(patient["id"]),
            "open_invoices": billing.open_invoices(patient["id"]),
            "drafts": Invoice.objects.filter(patient_id=patient["id"], status=DocStatus.DRAFT),
            "refundable": billing.refundable_invoices(patient["id"]),
            "receipts": Receipt.objects.filter(patient_id=patient["id"]).exclude(status=DocStatus.DRAFT)[:10],
            "pending_refunds": Refund.objects.filter(patient_id=patient["id"], status=DocStatus.DRAFT),
            "tender_form": TenderForm(initial={"token": token}),
            "invoice_form": InvoiceForm(initial={"token": token}),
            "refund_form": _refund_form(patient, initial={"token": token}),
        })
    return render(request, "finance/cashier.html", context)


def _refund_form(patient, *args, **kwargs):
    form = RefundForm(*args, **kwargs)
    choices = [(i.pk, f"{i.number} (پرداختی {i.paid_amount:,})") for i in
               Invoice.objects.filter(patient_id=patient["id"], paid_amount__gt=0).exclude(status=DocStatus.DRAFT)]
    form.fields["invoice"].choices = [("", "از علی‌الحساب")] + choices
    return form


def _back(patient):
    from django.urls import reverse

    return redirect(f"{reverse('finance:cashier')}?patient={patient['id']}")


@staff_member_required
@require_POST
def cashier_receive(request):
    _feature("finance.billing")
    _require(request.user, CASHIER_PERM)
    _require(request.user, "finance.post_receipt")
    patient = _patient_from_request(request)
    if not patient:
        raise Http404
    form = TenderForm(request.POST)
    if not form.is_valid() or not form.tenders():
        messages.error(request, "مبلغ پرداخت را وارد کنید.")
        return _back(patient)
    selected = [int(x) for x in request.POST.getlist("invoices") if x.isdigit()]
    # فقط صورتحساب‌های همین بیمار؛ شناسه ارسالی کاربر بدون بررسی مالکیت استفاده نمی‌شود.
    invoices = list(billing.open_invoices(patient["id"]).filter(pk__in=selected)) if selected else []
    try:
        receipt = billing.receive(patient_id=patient["id"], patient_name=patient["name"], tenders=form.tenders(),
                                  invoices=invoices, user=request.user, idempotency_key=f"cashier-{form.cleaned_data['token']}")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, f"دریافت {receipt.number} به مبلغ {receipt.total:,} ریال ثبت شد"
                         + (f"؛ {receipt.advance:,} ریال علی‌الحساب" if receipt.advance else "") + ".")
    return _back(patient)


@staff_member_required
@require_POST
def cashier_invoice(request):
    _feature("finance.billing")
    _require(request.user, CASHIER_PERM)
    _require(request.user, "finance.add_invoice")
    patient = _patient_from_request(request)
    if not patient:
        raise Http404
    form = InvoiceForm(request.POST)
    if not form.is_valid():
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
        return _back(patient)
    data = form.cleaned_data
    if data["discount"] and not request.user.has_perm("finance.give_invoice_discount"):
        messages.error(request, "مجوز دادن تخفیف ندارید.")
        return _back(patient)
    if Invoice.objects.filter(note=f"cashier-{data['token']}").exists():
        return _back(patient)
    doctor_id = data.get("doctor")
    try:
        invoice = billing.create_invoice(
            patient_id=patient["id"], patient_name=patient["name"], provider_id=doctor_id,
            provider_name=adapters.doctor_name(doctor_id), lines=data["lines"], discount_reason=data["discount_reason"],
            user=request.user, post=request.user.has_perm("finance.post_invoice"), note=f"cashier-{data['token']}",
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, f"صورتحساب {invoice.number or 'پیش‌نویس'} به مبلغ {invoice.patient_share:,} ریال صادر شد.")
    return _back(patient)


@staff_member_required
@require_POST
def cashier_refund(request):
    _feature("finance.billing")
    _require(request.user, CASHIER_PERM)
    _require(request.user, "finance.add_refund")
    patient = _patient_from_request(request)
    if not patient:
        raise Http404
    form = _refund_form(patient, request.POST)
    if not form.is_valid():
        messages.error(request, "اطلاعات استرداد کامل نیست.")
        return _back(patient)
    data = form.cleaned_data
    invoice = Invoice.objects.filter(pk=data["invoice"], patient_id=patient["id"]).first() if data.get("invoice") else None
    refund = billing.request_refund(
        invoice=invoice, patient_id=patient["id"], patient_name=patient["name"], amount=data["amount"], method=data["method"],
        reference_no=data["reference_no"], reason=data["reason"], user=request.user,
        idempotency_key=f"cashier-{data['token']}",
    )
    messages.info(request, f"درخواست استرداد {refund.amount:,} ریال ثبت شد؛ پس از تأیید، پرداخت آن را ثبت کنید.")
    return _back(patient)


@staff_member_required
@require_POST
def refund_pay(request, pk):
    _feature("finance.billing")
    _require(request.user, "finance.post_refund")
    refund = get_object_or_404(Refund, pk=pk)
    try:
        refund.post(request.user)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, f"استرداد {refund.number} پرداخت و ثبت شد.")
    return redirect(request.POST.get("next") or "finance:dashboard")


# ---------------------------------------------------------------- صورتحساب و مطالبات
@staff_member_required
def invoices(request):
    _feature("finance.billing")
    _require(request.user, "finance.view_invoice")
    qs = Invoice.objects.select_related("cost_center").order_by("-date", "-id")
    status = request.GET.get("status")
    if status in DocStatus.values:
        qs = qs.filter(status=status)
    module = request.GET.get("module")
    if module in Invoice.Module.values:
        qs = qs.filter(module=module)
    q = request.GET.get("q", "").strip()[:60]
    if q:
        qs = qs.filter(Q(number__icontains=q) | Q(patient_name__icontains=q) | Q(source_ref=q))
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(request, "finance/invoices.html", {"page": page, "q": q, "status": status, "module": module,
                                                     "statuses": DocStatus.choices, "modules": Invoice.Module.choices})


@staff_member_required
def invoice_detail(request, pk):
    _feature("finance.billing")
    _require(request.user, "finance.view_invoice")
    invoice = get_object_or_404(Invoice.objects.select_related("cost_center", "insurer"), pk=pk)
    entries = acc.JournalEntry.objects.filter(source_type="finance.invoice", source_id=invoice.pk).prefetch_related("lines__account")
    return render(request, "finance/invoice.html", {
        "invoice": invoice, "lines": invoice.lines.all(), "allocations": invoice.allocations.select_related("receipt"),
        "refunds": invoice.refunds.all(), "entries": entries, "event": invoice.event,
    })


@staff_member_required
def receivables(request):
    _feature("finance.billing")
    _require(request.user, FINANCE_PERM)
    return render(request, "finance/receivables.html", {"aging": billing.receivables_aging()})


# ---------------------------------------------------------------- گزارش‌ها
@staff_member_required
def ledger(request):
    _require(request.user, FINANCE_PERM)
    today = timezone.localdate()
    start, end = _date_param(request, "start", _month_start(today)), _date_param(request, "end", today)
    account = Account.objects.filter(code=request.GET.get("account", "")).first()
    center = CostCenter.objects.filter(code=request.GET.get("center", "")).first()
    report = acc.account_ledger(account, start, end, center) if account else None
    return render(request, "finance/ledger.html", {"accounts": Account.objects.all(), "centers": CostCenter.objects.all(),
                                                   "account": account, "center": center, "report": report,
                                                   "start": start, "end": end})


@staff_member_required
def journal(request):
    _require(request.user, FINANCE_PERM)
    today = timezone.localdate()
    start, end = _date_param(request, "start", today), _date_param(request, "end", today)
    page = Paginator(acc.journal_book(start, end), 30).get_page(request.GET.get("page"))
    return render(request, "finance/journal.html", {"page": page, "start": start, "end": end})


@staff_member_required
def center_pnl(request):
    _require(request.user, FINANCE_PERM)
    today = timezone.localdate()
    start, end = _date_param(request, "start", _month_start(today)), _date_param(request, "end", today)
    rows = acc.profit_and_loss_by_center(start, end)
    totals = {k: sum(r[k] for r in rows) for k in ("revenue", "expense", "profit")}
    return render(request, "finance/center_pnl.html", {"rows": rows, "totals": totals, "start": start, "end": end})


@staff_member_required
def cash_flow(request):
    _require(request.user, FINANCE_PERM)
    today = timezone.localdate()
    start, end = _date_param(request, "start", _month_start(today)), _date_param(request, "end", today)
    return render(request, "finance/cash_flow.html", {"report": acc.cash_flow(start, end), "start": start, "end": end})


@staff_member_required
def bank_reconciliation(request, pk):
    _require(request.user, "accounting.change_bankreconciliation")
    rec = get_object_or_404(BankReconciliation.objects.select_related("account"), pk=pk)
    if request.method == "POST":
        try:
            if "finalize" in request.POST:
                acc.finalize_reconciliation(rec, request.user, request.POST.get("note", ""))
                messages.success(request, "مغایرت‌گیری نهایی شد.")
            else:
                ids = [int(x) for x in request.POST.getlist("lines") if x.isdigit()]
                n = acc.mark_cleared(rec, ids, request.POST.get("bank_reference", ""))
                messages.success(request, f"{n} آرتیکل تطبیق داده شد.")
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        return redirect("finance:bank_reconciliation", pk=rec.pk)
    return render(request, "finance/bank_reconciliation.html", {"rec": rec, "summary": acc.reconciliation_summary(rec)})


@staff_member_required
def checks(request):
    _require(request.user, FINANCE_PERM)
    return render(request, "finance/checks.html", {"results": reconcile.run_checks()})


# ---------------------------------------------------------------- اتصال‌ها و تنظیمات
@staff_member_required
def integration(request):
    _require(request.user, "core.view_outboxevent")
    qs = OutboxEvent.objects.all()
    status = request.GET.get("status")
    if status in OutboxEvent.Status.values:
        qs = qs.filter(status=status)
    event_type = request.GET.get("type", "").strip()[:80]
    if event_type:
        qs = qs.filter(event_type=event_type)
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    subs = [{"type": t, "flag": s.flag, "enabled": not s.flag or flags.is_enabled(s.flag)} for t, s in sorted(events.subscriptions().items())]
    return render(request, "finance/integration.html", {
        "page": page, "status": status, "event_type": event_type, "statuses": OutboxEvent.Status.choices,
        "health": events.health(), "subscriptions": subs,
        "types": OutboxEvent.objects.values("event_type").annotate(n=Count("id")).order_by("event_type"),
    })


@staff_member_required
@require_POST
def integration_retry(request):
    _require(request.user, "core.retry_outboxevent")
    ids = [int(x) for x in request.POST.getlist("events") if x.isdigit()]
    qs = OutboxEvent.objects.filter(pk__in=ids) if ids else OutboxEvent.objects.filter(status=OutboxEvent.Status.DEAD)
    count = events.retry(qs)
    stats = events.dispatch(limit=200)
    messages.success(request, f"{count} رویداد به صف برگشت؛ پردازش: {stats['processed']} موفق، {stats['failed'] + stats['dead']} ناموفق.")
    return redirect("finance:integration")


@staff_member_required
def settings_view(request):
    _require(request.user, "core.change_featureflag")
    if request.method == "POST":
        key = request.POST.get("key", "")
        enabled = request.POST.get("enabled") == "1"
        try:
            flags.set_flag(key, enabled, request.user, request.POST.get("note", ""))
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, f"{key} {'روشن' if enabled else 'خاموش'} شد. داده‌ها حذف نشده‌اند.")
            if enabled:
                stats = events.dispatch(limit=500)
                if stats["processed"]:
                    messages.info(request, f"{stats['processed']} رویداد معوق پردازش شد.")
        return redirect("finance:settings")
    rows = flags.status()
    held = events.health()["by_type"]
    subs = events.subscriptions()
    for row in rows:
        types = [t for t, s in subs.items() if s.flag == row["key"]]
        row["pending"] = sum(r["n"] for r in held if r["event_type"] in types)
    return render(request, "finance/settings.html", {"rows": rows})


def health(request):
    """وضعیت سلامت: عمومی فقط ok/error؛ کارکنان مجاز جزئیات صف و قابلیت‌ها را می‌بینند (بدون داده بیمار)."""
    from django.db import connection

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        db_ok = True
    except Exception:  # noqa: BLE001 - فقط وضعیت گزارش می‌شود
        db_ok = False
    data = {"status": "ok" if db_ok else "error", "database": db_ok}
    user = request.user
    if user.is_authenticated and (user.is_superuser or user.has_perm("core.view_outboxevent")):
        q = events.health()
        data.update({"events": {k: q[k] for k in ("pending", "failed", "dead", "held", "oldest_pending_minutes")},
                     "flags": {r["key"]: r["effective"] for r in flags.status()}})
        if q["dead"] or q["oldest_pending_minutes"] > 60:
            data["status"] = "degraded"
    return JsonResponse(data, status=200 if db_ok else 503)


# ---------------------------------------------------------------- پزشک: سهم و تسویه خودم
@login_required
def my_earnings(request):
    _feature("finance.settlements")
    provider_id = adapters.provider_id_for(request.user)
    if provider_id is None:
        raise PermissionDenied
    contracts = list(settlements.provider_contracts(provider_id).exclude(status=Contract.Status.DRAFT))
    import jdatetime

    today = jdatetime.date.fromgregorian(date=timezone.localdate())
    previews = []
    for contract in contracts:
        try:
            rows = settlements.compute(contract, today.year, today.month)
        except ValidationError:
            rows = []
        previews.append({"contract": contract, "rows": rows, "total": sum(r["amount"] for r in rows)})
    posted = ProviderSettlement.objects.filter(contract__in=contracts).exclude(status=DocStatus.DRAFT).order_by("-year", "-month")
    return render(request, "finance/my_earnings.html", {"previews": previews, "settlements": posted,
                                                        "year": today.year, "month": today.month})


@login_required
def my_settlement(request, pk):
    _feature("finance.settlements")
    settlement = get_object_or_404(ProviderSettlement.objects.select_related("contract"), pk=pk)
    is_owner = settlement.contract.provider_id and settlement.contract.provider_id == adapters.provider_id_for(request.user)
    if not (is_owner and settlement.status != DocStatus.DRAFT) and not request.user.has_perm("finance.view_providersettlement"):
        raise Http404
    return render(request, "finance/settlement.html", {"settlement": settlement, "lines": settlement.lines.all()})


# ---------------------------------------------------------------- کارمند: فیش حقوق
@login_required
def my_payslips(request):
    _feature("finance.payroll")
    return render(request, "finance/my_payslips.html", {"lines": payroll.payslips_for(request.user)})


@login_required
def payslip(request, pk):
    _feature("finance.payroll")
    line = get_object_or_404(PayrollLine.objects.select_related("run", "profile__party"), pk=pk)
    own = line.profile.user_id == request.user.pk and line.run.status == DocStatus.POSTED
    if not own and not request.user.has_perm("finance.view_payroll"):
        raise Http404
    items = line.items.select_related("item_type")
    return render(request, "finance/payslip.html", {"line": line, "items": items})

"""API داخلی نسخه ۱ مالی (JSON) برای کارکنان و سامانه‌های داخلی.

- همه نقاط نیازمند ورود و مجوز سمت سرورند؛ پاسخ خطا همیشه {"error": {"code", "message"}} است و جزئیات فنی برنمی‌گرداند.
- فهرست‌ها صفحه‌بندی دارند (?page=، ?page_size= حداکثر ۱۰۰) و فیلتر ساده.
- ثبت دریافت (POST) با نشست کاربر و توکن CSRF (هدر X-CSRFToken) و هدر Idempotency-Key می‌خواهد؛ تکرار همان کلید همان دریافت را برمی‌گرداند و پول دوبار ثبت نمی‌شود.
  مبلغ صورتحساب‌ها از سمت سرور خوانده می‌شود و تخصیص بیش از مانده پذیرفته نمی‌شود.
"""

import functools
import json

from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_http_methods

from apps.accounting import services as acc
from apps.accounting.models import Account
from apps.audit.documents import DocStatus
from apps.core import events, flags
from apps.core.models import OutboxEvent

from . import adapters
from .models import Invoice, ProviderSettlement, TenderMethod
from .services import billing

VERSION = "v1"


def error(code, message, status):
    return JsonResponse({"error": {"code": code, "message": message}}, status=status)


def api_view(*perms, methods=("GET",)):
    def decorator(view):
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return error("unauthenticated", "ابتدا وارد شوید.", 401)
            if perms and not request.user.is_superuser and not any(request.user.has_perm(p) for p in perms):
                return error("forbidden", "مجوز این عملیات را ندارید.", 403)
            if not flags.is_enabled("finance"):
                return error("disabled", "ماژول مالی خاموش است.", 503)
            try:
                return view(request, *args, **kwargs)
            except ValidationError as exc:
                return error("invalid", " ".join(exc.messages), 400)
            except (KeyError, ValueError, TypeError, json.JSONDecodeError):
                return error("bad_request", "داده ورودی نامعتبر است.", 400)
        return require_http_methods(list(methods))(wrapper)
    return decorator


def paginate(request, qs, serialize):
    try:
        size = max(1, min(int(request.GET.get("page_size", 20)), 100))
    except ValueError:
        size = 20
    page = Paginator(qs, size).get_page(request.GET.get("page"))
    return {"count": page.paginator.count, "page": page.number, "pages": page.paginator.num_pages,
            "results": [serialize(obj) for obj in page.object_list]}


def invoice_json(invoice, lines=False):
    data = {
        "id": invoice.pk, "number": invoice.number, "status": invoice.status, "date": str(invoice.date), "module": invoice.module,
        "patient_id": invoice.patient_id, "patient_name": invoice.patient_name, "provider_id": invoice.provider_id,
        "source": {"type": invoice.source_type, "id": invoice.source_id, "ref": invoice.source_ref},
        "currency": "IRR", "gross": invoice.gross, "discount": invoice.discount, "insurer_share": invoice.insurer_share,
        "patient_share": invoice.patient_share, "paid": invoice.paid_amount, "balance": invoice.balance_due,
        "payment_status": invoice.payment_status,
    }
    if lines:
        data["lines"] = [{"service_code": l.service_code, "description": l.description, "quantity": l.quantity,
                          "unit_price": l.unit_price, "discount": l.discount, "insurer_share": l.insurer_share,
                          "provider_id": l.provider_id} for l in invoice.lines.all()]
    return data


@require_GET
def health(request):
    from .views import health as html_health

    return html_health(request)


@api_view("finance.view_invoice")
def invoices(request):
    qs = Invoice.objects.order_by("-date", "-id")
    for field in ("status", "module"):
        if request.GET.get(field):
            qs = qs.filter(**{field: request.GET[field]})
    if request.GET.get("patient_id"):
        qs = qs.filter(patient_id=int(request.GET["patient_id"]))
    return JsonResponse({"version": VERSION, **paginate(request, qs, invoice_json)})


@api_view("finance.view_invoice")
def invoice_detail(request, number):
    invoice = Invoice.objects.filter(number=number).first()
    if invoice is None:
        return error("not_found", "صورتحساب پیدا نشد.", 404)
    return JsonResponse({"version": VERSION, "invoice": invoice_json(invoice, lines=True)})


@api_view("finance.post_receipt", methods=("POST",))
def receipts(request):
    """{"patient_id": 12, "tenders": [{"method": "card", "amount": 500000, "reference_no": "..."}], "invoices": ["INV-..."]}"""
    key = request.headers.get("Idempotency-Key", "").strip()
    if not 8 <= len(key) <= 64:
        return error("idempotency_key_required", "هدر Idempotency-Key (۸ تا ۶۴ نویسه) لازم است.", 400)
    body = json.loads(request.body or b"{}")
    patient = adapters.get_patient(int(body["patient_id"]))
    if patient is None:
        return error("not_found", "بیمار پیدا نشد.", 404)
    tenders = []
    for t in body["tenders"]:
        if t["method"] not in TenderMethod.values or t["method"] == TenderMethod.ONLINE:
            return error("invalid", "روش پرداخت نامعتبر است.", 400)
        tenders.append({"method": t["method"], "amount": int(t["amount"]), "reference_no": str(t.get("reference_no", ""))})
    numbers = [str(n) for n in body.get("invoices", [])]
    open_ = billing.open_invoices(patient["id"])
    selected = list(open_.filter(number__in=numbers))
    if len(selected) != len(set(numbers)):
        return error("invalid", "برخی صورتحساب‌ها باز نیستند یا متعلق به این بیمار نیستند.", 400)
    receipt = billing.receive(patient_id=patient["id"], patient_name=patient["name"], tenders=tenders, invoices=selected,
                              user=request.user, idempotency_key=f"api-{request.user.pk}-{key}")
    return JsonResponse({"version": VERSION, "receipt": {
        "number": receipt.number, "status": receipt.status, "total": receipt.total, "advance": receipt.advance,
        "allocations": [{"invoice": a.invoice.number, "amount": a.amount} for a in receipt.allocations.select_related("invoice")],
    }}, status=201)


@api_view("accounting.view_financial_reports", "accounting.view_account")
def accounts(request):
    rows = [{"code": a.code, "name": a.name, "type": a.type, "is_cash": a.is_cash, "active": a.is_active}
            for a in Account.objects.order_by("code")]
    return JsonResponse({"version": VERSION, "results": rows})


@api_view("accounting.view_financial_reports")
def trial_balance(request):
    rows, totals = acc.trial_balance(request.GET.get("start") or None, request.GET.get("end") or None)
    return JsonResponse({"version": VERSION, "currency": "IRR", "rows": rows, "totals": totals,
                         "balanced": totals["debit"] == totals["credit"]})


@api_view("core.view_outboxevent")
def integration_status(request):
    qs = OutboxEvent.objects.all()
    if request.GET.get("status"):
        qs = qs.filter(status=request.GET["status"])
    serialize = lambda e: {  # noqa: E731
        "event_id": str(e.event_id), "type": e.event_type, "version": e.schema_version, "source": f"{e.source_type}#{e.source_id}",
        "status": e.status, "attempts": e.attempts, "error": e.last_error, "result": e.result_ref,
        "occurred_at": e.occurred_at.isoformat(), "correlation_id": e.correlation_id,
    }
    return JsonResponse({"version": VERSION, "health": events.health(), "flags": flags.status(), **paginate(request, qs, serialize)})


@api_view()
def my_settlements(request):
    provider_id = adapters.provider_id_for(request.user)
    if provider_id is None:
        return error("forbidden", "این بخش فقط برای پزشکان و ارائه‌دهندگان است.", 403)
    qs = ProviderSettlement.objects.filter(contract__provider_id=provider_id).exclude(status=DocStatus.DRAFT).order_by("-year", "-month")
    serialize = lambda s: {"number": s.number, "year": s.year, "month": s.month, "total": s.total, "status": s.status}  # noqa: E731
    return JsonResponse({"version": VERSION, **paginate(request, qs, serialize)})

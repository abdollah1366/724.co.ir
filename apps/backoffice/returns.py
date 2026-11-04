"""مرجوعی فروش از پیشخوان: شماره فاکتور را بزنید، تعداد برگشتی هر ردیف و علت را بنویسید، ثبت.

پیش از این مرجوعی فقط در پنل مدیریت و با انتخاب شناسه ردیف‌های فاکتور ممکن بود. سند همان SalesReturn است و همان
کنترل‌ها (سقف تعداد قابل برگشت، علت الزامی، بچ ناسالم ← قرنطینه، سند حسابداری) اعمال می‌شود.
"""

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from apps.audit.documents import DocStatus
from apps.core.validators import normalize_digits
from apps.sales import cashbox
from apps.sales.models import SalesInvoice, SalesReturn, SalesReturnLine

RETURN_PERM = "sales.post_salesreturn"


def _require(user):
    if not (user.is_superuser or user.has_perm(RETURN_PERM)):
        raise PermissionDenied


@staff_member_required
def find_invoice(request):
    _require(request.user)
    number = normalize_digits(request.GET.get("number", "")).strip().upper()
    if number:
        invoice = (SalesInvoice.objects.filter(status=DocStatus.POSTED, number__iendswith=number).order_by("-posted_at").first()
                   if len(number) >= 3 else None)
        if invoice:
            return redirect("backoffice:sales_return", pk=invoice.pk)
        messages.error(request, f"فاکتور فروش ثبت‌شده‌ای با شماره «{number}» پیدا نشد.")
    recent = SalesInvoice.objects.filter(status=DocStatus.POSTED).order_by("-posted_at")[:10]
    return render(request, "backoffice/return_find.html", {"number": number, "recent": recent})


@staff_member_required
def sales_return(request, pk):
    user = request.user
    _require(user)
    invoice = get_object_or_404(SalesInvoice, pk=pk)
    lines = [(line, line.quantity - line.returned_quantity) for line in invoice.lines.select_related("product").order_by("pk")]
    if request.method == "POST":
        try:
            with transaction.atomic():
                method = request.POST.get("refund_method")
                if method not in SalesReturn.Refund.values:
                    raise ValidationError("روش استرداد را انتخاب کنید.")
                ret = SalesReturn.objects.create(
                    invoice=invoice, reason=request.POST.get("reason", "").strip()[:300], refund_method=method,
                    restock=bool(request.POST.get("restock")), created_by=user,
                    session=cashbox.user_session(user, cashbox.Department.PHARMACY),
                )
                for line, _left in lines:
                    raw = normalize_digits(request.POST.get(f"q{line.pk}", "")).strip()
                    qty = int(raw) if raw.isdigit() else 0
                    if qty:
                        SalesReturnLine.objects.create(document=ret, invoice_line=line, quantity=qty)
                ret.post(user)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            where = "به قفسه فروش" if ret.restock else "به انبار قرنطینه"
            messages.success(request, f"مرجوعی {ret.number} ثبت شد؛ کالا {where} برگشت و {ret.refund_amount:,} ریال "
                                      f"({ret.get_refund_method_display()}) به مشتری برگردانده شود.")
            return redirect("backoffice:pos_invoice", pk=invoice.pk)
    return render(request, "backoffice/return.html", {
        "invoice": invoice, "lines": lines, "methods": SalesReturn.Refund.choices, "post": request.POST,
        "returns": invoice.returns.filter(status=DocStatus.POSTED),
    })

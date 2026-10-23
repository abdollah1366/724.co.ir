"""داشبورد لحظه‌ای آزمایشگاه."""

from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Sum
from django.shortcuts import render
from django.utils import timezone

from apps.accounting import services as acc
from apps.audit.documents import DocStatus

from ..models import LabOrder, LabPayment, LabResult, SamplingAppointment, Specimen
from ..services import lis
from .staff import staff_perm


def dashboard_data(user, today=None):
    today = today or timezone.localdate()
    posted = LabOrder.objects.filter(status=DocStatus.POSTED)
    data = {
        "today": today,
        "orders_today": posted.filter(date=today).count(),
        "appointments_today": SamplingAppointment.objects.active().filter(date=today).count(),
        "awaiting_sampling": Specimen.objects.filter(order__status=DocStatus.POSTED, status=Specimen.Status.PENDING).count(),
        "awaiting_result": LabResult.objects.filter(item__order__status=DocStatus.POSTED, status=LabResult.Status.PENDING)
        .exclude(specimen__status__in=[Specimen.Status.PENDING, Specimen.Status.REJECTED]).count(),
        "awaiting_approval": LabResult.objects.filter(item__order__status=DocStatus.POSTED, status=LabResult.Status.ENTERED).count(),
        "ready_not_delivered": posted.filter(progress=LabOrder.Progress.READY).count(),
        "urgent_open": posted.filter(priority=LabOrder.Priority.URGENT).exclude(
            progress__in=[LabOrder.Progress.READY, LabOrder.Progress.DELIVERED]
        ).count(),
        "critical": lis.unnotified_critical()[:20],
        "by_progress": posted.filter(date=today).values("progress").annotate(n=Count("id")),
    }
    tat = (
        LabResult.objects.filter(status=LabResult.Status.APPROVED, approved_at__date=today)
        .annotate(tat=ExpressionWrapper(F("approved_at") - F("item__order__posted_at"), output_field=DurationField()))
        .aggregate(avg=Avg("tat"))["avg"]
    )
    data["tat_hours"] = round(tat.total_seconds() / 3600, 1) if tat else None
    if user.has_perm("accounting.view_financial_reports"):
        today_orders = posted.filter(date=today).aggregate(
            gross=Sum("gross_amount"), basic=Sum("basic_share"), supp=Sum("supplementary_share"), discount=Sum("discount")
        )
        payments = LabPayment.objects.filter(status=DocStatus.POSTED, date=today)
        received = payments.filter(direction=LabPayment.Direction.RECEIVE).aggregate(t=Sum("amount"))["t"] or 0
        refunded = payments.filter(direction=LabPayment.Direction.REFUND).aggregate(t=Sum("amount"))["t"] or 0
        data["finance"] = {
            **{k: v or 0 for k, v in today_orders.items()}, "cash_in": received - refunded,
            "insurer_receivable": sum(r["receivable"] for r in acc.party_balances(kind="insurer")),
        }
    return data


@staff_perm("lab.view_laborder")
def dashboard(request):
    return render(request, "lab/staff/dashboard.html", dashboard_data(request.user))

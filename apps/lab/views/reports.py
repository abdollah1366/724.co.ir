"""گزارش مدیریتی آزمایشگاه برای یک بازه: پذیرش، بیمه‌ها، پرتکرارترین آزمایش‌ها، زمان جواب‌دهی، صف و رضایت."""

from django.db.models import Count, F, Q, Sum
from django.shortcuts import render
from django.utils import timezone

from apps.audit.documents import DocStatus

from ..models import LabOrder, LabOrderItem, LabResult
from ..services import crm, queue
from .finance import _parse
from .staff import staff_perm


def tat_rows(start, end):
    """زمان جواب‌دهی هر بخش: از ثبت پذیرش تا تأیید، و تعداد جواب‌های دیرتر از زمان اعلام‌شده آزمایش."""
    results = LabResult.objects.filter(
        status=LabResult.Status.APPROVED, item__order__date__range=(start, end), item__order__status=DocStatus.POSTED
    ).select_related("item__test__section", "item__order")
    sections = {}
    for r in results:
        hours = (r.approved_at - r.item.order.posted_at).total_seconds() / 3600
        row = sections.setdefault(r.item.test.section.name, {"n": 0, "total": 0.0, "late": 0})
        row["n"] += 1
        row["total"] += hours
        row["late"] += hours > r.item.test.turnaround_hours
    return [
        {"section": name, "n": row["n"], "avg": round(row["total"] / row["n"], 1), "late": row["late"]}
        for name, row in sorted(sections.items())
    ]


@staff_perm("lab.view_laborder")
def reports(request):
    today = timezone.localdate()
    start = _parse(request.GET.get("start"), today.replace(day=1))
    end = _parse(request.GET.get("end"), today)
    orders = LabOrder.objects.filter(status=DocStatus.POSTED, date__range=(start, end))
    context = {
        "start": start, "end": end,
        "summary": orders.aggregate(
            n=Count("id"), urgent=Count("id", filter=Q(priority=LabOrder.Priority.URGENT)),
            home=Count("id", filter=Q(home_fee__gt=0)), delivered=Count("id", filter=Q(progress=LabOrder.Progress.DELIVERED)),
        ),
        "top_tests": LabOrderItem.objects.filter(order__in=orders).values("test__code", "test__name")
        .annotate(n=Count("id")).order_by("-n")[:20],
        "tat": tat_rows(start, end),
        "queue": queue.stats(start, end),
        "feedback": crm.feedback_summary(start),
    }
    if request.user.has_perm("accounting.view_financial_reports"):
        context["money"] = orders.aggregate(
            gross=Sum("gross_amount"), basic=Sum("basic_share"), supp=Sum("supplementary_share"),
            patient=Sum("patient_share"), discount=Sum("discount"), home=Sum("home_fee"),
        )
        context["by_plan"] = [
            *orders.filter(basic_plan__isnull=False).values(name=F("basic_plan__name"))
            .annotate(n=Count("id"), share=Sum("basic_share")).order_by("-share"),
            *orders.filter(supplementary_plan__isnull=False).values(name=F("supplementary_plan__name"))
            .annotate(n=Count("id"), share=Sum("supplementary_share")).order_by("-share"),
        ]
    return render(request, "lab/staff/reports.html", context)

"""مشاور آزمایشگاه: فرم درخواست مشاوره، صندوق پیگیری کارشناس و نظرسنجی رضایت."""

import datetime

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.audit import log
from apps.audit.context import client_ip
from apps.audit.documents import DocStatus
from apps.audit.models import AuditLog
from apps.core.validators import normalize_digits

from ..forms import ConsultForm, FeedbackForm, InteractionForm
from ..models import ConsultRequest, Feedback, LabOrder
from ..services import crm
from ..services.portal import rate_limited
from .staff import _errors, staff_perm

DONE_KEY = "lab_consult_done"


def consult(request):
    initial = {"topic": request.GET["topic"]} if request.GET.get("topic") in ConsultRequest.Topic.values else {}
    if request.user.is_authenticated:
        initial["full_name"] = request.user.get_full_name()
    form = ConsultForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        if rate_limited(f"consult:{client_ip(request)}", limit=settings.LAB_RULES["consult_limit_per_hour"]):
            messages.error(request, "تعداد درخواست‌ها زیاد است؛ لطفاً تلفنی تماس بگیرید.")
            return redirect("lab:consult")
        item = form.save(commit=False)
        item.user = request.user if request.user.is_authenticated else None
        item.save()
        crm.request_received(item)
        request.session[DONE_KEY] = item.tracking_code
        return redirect("lab:consult_done")
    return render(request, "lab/consult.html", {"form": form})


def consult_done(request):
    code = request.session.get(DONE_KEY)
    if not code:
        return redirect("lab:consult")
    return render(request, "lab/consult_done.html", {"code": code})


def feedback(request, token):
    item = get_object_or_404(Feedback.objects.select_related("order"), token=token)
    if not item.is_open:
        return render(request, "lab/feedback.html", {"closed": True})
    form = FeedbackForm(request.POST or None, instance=item)
    if request.method == "POST" and form.is_valid():
        try:
            crm.submit_feedback(item, form.cleaned_data)
        except ValidationError as exc:
            _errors(request, exc)
        return render(request, "lab/feedback.html", {"thanks": True})
    return render(request, "lab/feedback.html", {"form": form})


# --- پیشخوان مشاور ---
@staff_perm("lab.view_consultrequest")
def inbox(request):
    view = request.GET.get("view", "open")
    items = ConsultRequest.objects.select_related("assigned_to", "patient")
    if view == "mine":
        items = items.open().filter(assigned_to=request.user)
    elif view == "unassigned":
        items = items.open().filter(assigned_to__isnull=True)
    elif view == "overdue":
        items = items.overdue()
    elif view == "followup":
        items = items.open().filter(next_follow_up__lte=timezone.now())
    elif view == "all":
        pass
    else:
        view, items = "open", items.open()
    if request.GET.get("topic") in ConsultRequest.Topic.values:
        items = items.filter(topic=request.GET["topic"])
    q = normalize_digits(request.GET.get("q", "")).strip()
    if q:
        items = items.filter(Q(mobile__contains=q) | Q(full_name__icontains=q) | Q(tracking_code__iexact=q))
    form = ConsultForm(request.POST or None, staff=True)
    if request.method == "POST" and request.user.has_perm("lab.add_consultrequest") and form.is_valid():
        new = form.save()
        crm.log_interaction(new, "note", "ثبت توسط کارشناس", request.user)
        return redirect("lab:crm_detail", pk=new.pk)
    base = ConsultRequest.objects
    return render(request, "lab/staff/crm_inbox.html", {
        "items": items.order_by("due_at")[:200] if view != "all" else items[:200], "view": view, "form": form,
        "topics": ConsultRequest.Topic.choices, "counts": {
            "open": base.open().count(), "mine": base.open().filter(assigned_to=request.user).count(),
            "unassigned": base.open().filter(assigned_to__isnull=True).count(), "overdue": base.overdue().count(),
            "followup": base.open().filter(next_follow_up__lte=timezone.now()).count(),
        },
    })


def _consultants():
    return get_user_model().objects.filter(is_active=True, is_staff=True).filter(
        Q(is_superuser=True) | Q(groups__permissions__codename="change_consultrequest")
        | Q(user_permissions__codename="change_consultrequest")
    ).distinct().order_by("username")


@staff_perm("lab.view_consultrequest")
def detail(request, pk):
    item = get_object_or_404(ConsultRequest.objects.select_related("assigned_to", "patient", "order"), pk=pk)
    log.record(AuditLog.Action.EVENT, item, message="مشاهده درخواست مشاوره")
    orders = []
    if item.patient and request.user.has_perm("lab.view_laborder"):
        orders = item.patient.orders.filter(status=DocStatus.POSTED).order_by("-date")[:10]
    return render(request, "lab/staff/crm_detail.html", {
        "item": item, "interactions": item.interactions.select_related("user"), "form": InteractionForm(),
        "consultants": _consultants(), "orders": orders,
        "feedback": getattr(item.order, "feedback", None) if item.order_id else None,
    })


@require_POST
@staff_perm("lab.change_consultrequest")
def action(request, pk):
    item = get_object_or_404(ConsultRequest, pk=pk)
    what = request.POST.get("action")
    try:
        if what == "log":
            form = InteractionForm(request.POST)
            if not form.is_valid():
                messages.error(request, "شرح و نوع پیگیری را کامل کنید.")
            else:
                data = form.cleaned_data
                follow = data["next_follow_up"]
                if follow and timezone.is_naive(follow):
                    follow = timezone.make_aware(follow)
                crm.log_interaction(item, data["kind"], data["text"], request.user, data["status"] or None, follow)
        elif what == "assign":
            assignee = get_object_or_404(_consultants(), pk=request.POST.get("user") or request.user.pk)
            crm.assign(item, assignee, request.user)
        elif what == "link":
            number = normalize_digits(request.POST.get("number", "")).strip().upper()
            order = LabOrder.objects.filter(number__iexact=number, status=DocStatus.POSTED).first()
            if order is None:
                messages.error(request, "پذیرشی با این شماره پیدا نشد.")
            elif order.patient.mobile != item.mobile:
                messages.error(request, "موبایل این پذیرش با موبایل درخواست یکی نیست؛ برای حفظ حریم خصوصی متصل نشد.")
            else:
                crm.link_order(item, order)
    except ValidationError as exc:
        _errors(request, exc)
    return redirect("lab:crm_detail", pk=pk)


@staff_perm("lab.view_feedback")
def feedback_list(request):
    since = timezone.localdate() - datetime.timedelta(days=30)
    return render(request, "lab/staff/feedback.html", {
        "summary": crm.feedback_summary(since), "since": since,
        "items": Feedback.objects.filter(submitted_at__isnull=False).select_related("order")[:100],
    })

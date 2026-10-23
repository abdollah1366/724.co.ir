"""LIS: نمونه، برچسب بارکد، کارتابل، ثبت و تأیید جواب، اصلاح، مقادیر بحرانی، کنترل کیفی و برگه جواب."""

import jdatetime
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_POST

from apps.audit import log
from apps.audit.documents import DocStatus
from apps.audit.models import AuditLog

from .. import barcode
from ..models import CriticalNotification, LabOrder, LabResult, LabSection, QCMaterial, Specimen
from ..services import lis
from ..services.report import build_report, trend_svg
from .staff import _errors, staff_perm


@require_POST
@staff_perm("lab.change_specimen")
def specimen_action(request, pk, action):
    specimen = get_object_or_404(Specimen.objects.select_related("order"), pk=pk)
    try:
        if action == "collect":
            lis.collect_specimen(specimen, request.user)
        elif action == "receive":
            lis.receive_specimen(specimen, request.user)
        elif action == "reject":
            new = lis.reject_specimen(specimen, request.POST.get("reason", ""), request.user)
            messages.warning(request, f"نمونه مردود شد؛ نمونه‌گیری مجدد با بارکد {new.barcode} لازم است.")
    except ValidationError as exc:
        _errors(request, exc)
    return redirect("lab:staff_order", pk=specimen.order_id)


@staff_perm("lab.view_laborder")
def labels(request, pk):
    order = get_object_or_404(LabOrder.objects.select_related("patient"), pk=pk, status=DocStatus.POSTED)
    specimens = order.specimens.exclude(status=Specimen.Status.REJECTED).select_related("specimen_type")
    items = [
        {
            "specimen": s, "svg": mark_safe(barcode.svg(s.barcode)),
            "tests": ", ".join(r.item.test.code for r in s.results.select_related("item__test")),
        }
        for s in specimens
    ]
    return render(request, "lab/staff/labels.html", {"order": order, "labels": items})


class ResultEntryForm(forms.Form):
    value = forms.CharField(required=False, max_length=500)
    comment = forms.CharField(required=False, max_length=500)


@staff_perm("lab.view_labresult")
def results(request, pk):
    order = get_object_or_404(LabOrder.objects.select_related("patient"), pk=pk)
    section = request.GET.get("section", "")
    qs = LabResult.objects.filter(item__order=order).select_related(
        "item__test__section", "specimen", "entered_by", "approved_by"
    ).order_by("item__test__section__order", "item__test__order")
    if section:
        qs = qs.filter(item__test__section__code=section)
    if request.method == "POST":
        action = request.POST.get("action")
        done, failed = 0, []
        for result in qs:
            try:
                if action == "save" and request.user.has_perm("lab.enter_labresult"):
                    value = request.POST.get(f"value_{result.pk}", "").strip()
                    comment = request.POST.get(f"comment_{result.pk}")
                    if value and result.status != LabResult.Status.APPROVED and (
                        value != result.value or (comment is not None and comment != result.comment)
                    ):
                        lis.enter_result(result, value, request.user, comment=comment)
                        done += 1
                elif action == "approve" and request.user.has_perm("lab.approve_labresult"):
                    if request.POST.get(f"approve_{result.pk}"):
                        lis.approve_result(result, request.user)
                        done += 1
            except ValidationError as exc:
                failed.append(" ".join(exc.messages))
        if done:
            messages.success(request, f"{done} جواب {'ثبت' if action == 'save' else 'تأیید'} شد.")
        for error in failed:
            messages.error(request, error)
        return redirect(f"{request.path}?section={section}" if section else request.path)
    rows = []
    for result in qs:
        prev = lis.previous_result(result)
        rows.append({"result": result, "previous": prev, "qc": lis.qc_status(result.item.test)})
    log.record(AuditLog.Action.EVENT, order, message="مشاهده جواب‌ها در پیشخوان", user=request.user)
    return render(request, "lab/staff/results.html", {
        "order": order, "rows": rows, "section": section,
        "sections": LabSection.objects.filter(tests__order_items__order=order).distinct(),
    })


class AmendForm(forms.Form):
    value = forms.CharField(label="جواب صحیح", max_length=500)
    reason = forms.CharField(label="علت اصلاح", max_length=300)


@staff_perm("lab.amend_labresult")
def amend(request, pk):
    result = get_object_or_404(LabResult.objects.select_related("item__order", "item__test"), pk=pk)
    form = AmendForm(request.POST or None, initial={"value": result.value})
    if request.method == "POST" and form.is_valid():
        try:
            lis.amend_result(result, form.cleaned_data["value"], form.cleaned_data["reason"], request.user)
        except ValidationError as exc:
            _errors(request, exc)
        else:
            messages.success(request, "جواب اصلاح شد و دوباره نیاز به تأیید دارد.")
            return redirect("lab:staff_results", pk=result.item.order_id)
    return render(request, "lab/staff/form_page.html", {
        "form": form, "title": f"اصلاح جواب {result.item.test.name} (پذیرش {result.item.order.number})",
        "note": f"جواب فعلی: {result.value} · نسخه {result.version}. نسخه فعلی با علت در تاریخچه می‌ماند.",
    })


class CriticalForm(forms.ModelForm):
    class Meta:
        model = CriticalNotification
        fields = ["recipient", "recipient_name", "phone", "read_back", "note"]


@staff_perm("lab.add_criticalnotification")
def critical_notify(request, pk):
    result = get_object_or_404(LabResult.objects.select_related("item__order__patient", "item__test"), pk=pk)
    patient = result.item.order.patient
    form = CriticalForm(request.POST or None, initial={"recipient": "patient", "recipient_name": patient.full_name, "phone": patient.mobile})
    if request.method == "POST" and form.is_valid():
        try:
            lis.notify_critical(result, request.user, **form.cleaned_data)
        except ValidationError as exc:
            _errors(request, exc)
        else:
            messages.success(request, "اطلاع‌رسانی مقدار بحرانی ثبت شد.")
            return redirect("lab:staff_results", pk=result.item.order_id)
    return render(request, "lab/staff/form_page.html", {
        "form": form, "title": f"اطلاع‌رسانی مقدار بحرانی: {result.item.test.name} = {result.value} {result.unit}",
        "note": f"پذیرش {result.item.order.number} · {patient.full_name}. پس از تماس، از گیرنده بخواهید مقدار را تکرار کند.",
        "history": result.critical_notifications.all(),
    })


@staff_perm("lab.view_labresult")
def worklist(request):
    status = request.GET.get("status", LabResult.Status.PENDING)
    section = request.GET.get("section", "")
    qs = LabResult.objects.filter(item__order__status=DocStatus.POSTED, status=status).select_related(
        "item__test", "item__order__patient", "specimen"
    )
    if status == LabResult.Status.PENDING:
        qs = qs.exclude(specimen__status__in=[Specimen.Status.PENDING, Specimen.Status.REJECTED])
    if section:
        qs = qs.filter(item__test__section__code=section)
    qs = qs.order_by("-item__order__priority", "item__order__posted_at")[:300]
    return render(request, "lab/staff/worklist.html", {
        "results": qs, "status": status, "section": section, "sections": LabSection.objects.filter(is_active=True),
        "statuses": [(LabResult.Status.PENDING, "منتظر جواب"), (LabResult.Status.ENTERED, "منتظر تأیید")],
        "critical": lis.unnotified_critical()[:50],
    })


class QCForm(forms.Form):
    material = forms.ModelChoiceField(label="ماده کنترل", queryset=QCMaterial.objects.filter(is_active=True))
    value = forms.CharField(label="مقدار", max_length=20)
    corrective_action = forms.CharField(label="اقدام اصلاحی (در صورت مردودی)", max_length=300, required=False)


@staff_perm("lab.add_qcresult")
def qc(request):
    form = QCForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            run = lis.record_qc(form.cleaned_data["material"], form.cleaned_data["value"], request.user,
                                form.cleaned_data["corrective_action"])
        except ValidationError as exc:
            _errors(request, exc)
        else:
            level = messages.SUCCESS if run.status == "accepted" else messages.WARNING if run.status == "warning" else messages.ERROR
            messages.add_message(request, level, f"{run.get_status_display()} (Z={run.z_score}) {run.violations}")
            return redirect("lab:staff_qc")
    today = timezone.localdate()
    materials = [
        {"material": m, "today": m.results.filter(run_at__date=today).first()}
        for m in QCMaterial.objects.filter(is_active=True).select_related("test")
    ]
    return render(request, "lab/staff/qc.html", {"form": form, "materials": materials})


@staff_perm("lab.view_qcresult")
def qc_chart(request, pk):
    material = get_object_or_404(QCMaterial.objects.select_related("test"), pk=pk)
    runs = list(material.results.all()[:30])[::-1]
    points = [(jdatetime.date.fromgregorian(date=timezone.localtime(r.run_at).date()).strftime("%m/%d"), r.value) for r in runs]
    sd2 = 2 * material.sd
    chart = mark_safe(trend_svg(points, low=material.mean - sd2, high=material.mean + sd2)) if points else ""
    return render(request, "lab/staff/qc_chart.html", {"material": material, "runs": runs[::-1], "chart": chart})


@staff_perm("lab.view_labresult")
def staff_report(request, pk):
    order = get_object_or_404(LabOrder.objects.select_related("patient"), pk=pk, status=DocStatus.POSTED)
    log.record(AuditLog.Action.EVENT, order, message="چاپ برگه جواب در پیشخوان", user=request.user)
    return render(request, "lab/report.html", {**build_report(order), "staff": True})

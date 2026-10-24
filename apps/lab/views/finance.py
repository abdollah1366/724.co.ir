"""مالی آزمایشگاه: صورت‌حساب و تسویه بیمه، خروجی CSV و گزارش صندوق روزانه."""

import csv
import datetime

import jdatetime
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.audit.documents import DocStatus
from apps.core.validators import normalize_digits

from ..models import ClaimSettlement, InsuranceClaim, InsurancePlan, LabPayment
from ..services import orders as order_service
from .staff import _errors, staff_perm


def _parse(value, default):
    value = normalize_digits(value or "")
    try:
        return datetime.date.fromisoformat(value) if value else default
    except ValueError:
        return default


class ClaimForm(forms.Form):
    plan = forms.ModelChoiceField(label="قرارداد", queryset=InsurancePlan.objects.all())
    start = forms.DateField(label="از تاریخ", widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(label="تا تاریخ", widget=forms.DateInput(attrs={"type": "date"}))


@staff_perm("lab.view_insuranceclaim")
def claims(request):
    form = ClaimForm(request.POST or None)
    if request.method == "POST" and request.user.has_perm("lab.add_insuranceclaim") and form.is_valid():
        data = form.cleaned_data
        claim = order_service.build_claim(data["plan"], data["start"], data["end"], request.user)
        if not claim.lines.exists():
            claim.delete()
            messages.info(request, "در این بازه پذیرش ثبت‌شده‌ای برای این قرارداد که قبلاً ارسال نشده باشد وجود ندارد.")
            return redirect("lab:staff_claims")
        return redirect("lab:staff_claim", pk=claim.pk)
    return render(request, "lab/staff/claims.html", {
        "form": form, "claims": InsuranceClaim.objects.select_related("plan").order_by("-id")[:100],
    })


class SettlementForm(forms.ModelForm):
    class Meta:
        model = ClaimSettlement
        fields = ["received", "deductions", "method", "reference_no", "note"]

    def __init__(self, data=None, *args, **kwargs):
        if data is not None:
            data = data.copy()
            for name in ("received", "deductions", "reference_no"):
                data[name] = normalize_digits(data.get(name, ""))
        super().__init__(data, *args, **kwargs)


@staff_perm("lab.view_insuranceclaim")
def claim_detail(request, pk):
    claim = get_object_or_404(InsuranceClaim.objects.select_related("plan__insurer"), pk=pk)
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "post" and request.user.has_perm("lab.post_insuranceclaim"):
                claim.post(request.user)
                messages.success(request, f"صورت‌حساب {claim.number} ثبت و ارسال شد.")
            elif action == "settle" and request.user.has_perm("lab.post_claimsettlement"):
                form = SettlementForm(request.POST)
                if form.is_valid():
                    settlement = form.save(commit=False)
                    settlement.claim = claim
                    settlement.created_by = request.user
                    settlement.save()
                    try:
                        settlement.post(request.user)
                    except ValidationError:
                        settlement.delete()
                        raise
                    messages.success(request, "تسویه ثبت شد.")
                else:
                    messages.error(request, "اطلاعات تسویه معتبر نیست.")
            elif action == "delete" and claim.is_draft:
                claim.delete()
                return redirect("lab:staff_claims")
        except ValidationError as exc:
            _errors(request, exc)
        return redirect("lab:staff_claim", pk=pk)
    lines = claim.lines.select_related("order__patient")
    if request.GET.get("format") == "csv":
        return _claim_csv(claim, lines)
    return render(request, "lab/staff/claim_detail.html", {
        "claim": claim, "lines": lines, "settlements": claim.settlements.all(), "form": SettlementForm(),
    })


def _claim_csv(claim, lines):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="claim-{claim.number or claim.pk}.csv"'
    response.write("﻿")  # برای نمایش درست فارسی در اکسل
    writer = csv.writer(response)
    basic = claim.plan.kind == InsurancePlan.Kind.BASIC
    writer.writerow(["شماره پذیرش", "تاریخ", "نام بیمار", "کد ملی", "شماره بیمه", "کد تأیید", "کد نسخه", "پزشک", "نظام پزشکی", "مبلغ"])
    for line in lines:
        o = line.order
        writer.writerow([
            o.number, jdatetime.date.fromgregorian(date=o.date).strftime("%Y/%m/%d"), o.patient.full_name,
            o.patient.identifier, o.basic_insurance_no if basic else o.supplementary_insurance_no,
            o.basic_approval_code if basic else o.supplementary_approval_code, o.prescription_code, o.referring_doctor,
            o.referring_council_no, line.amount,
        ])
    return response


@staff_perm("lab.view_labpayment")
def cashier_report(request):
    day = _parse(request.GET.get("date"), timezone.localdate())
    payments = LabPayment.objects.filter(status=DocStatus.POSTED, date=day)
    rows = payments.values("created_by__username", "method", "direction").annotate(total=Sum("amount"), n=Count("id")).order_by(
        "created_by__username", "method"
    )
    methods, directions = dict(LabPayment._meta.get_field("method").choices), dict(LabPayment.Direction.choices)
    rows = [{**r, "method": methods.get(r["method"], r["method"]), "direction": directions[r["direction"]]} for r in rows]
    received = payments.filter(direction=LabPayment.Direction.RECEIVE).aggregate(t=Sum("amount"))["t"] or 0
    refunded = payments.filter(direction=LabPayment.Direction.REFUND).aggregate(t=Sum("amount"))["t"] or 0
    return render(request, "lab/staff/cashier.html", {
        "day": day, "rows": rows, "received": received, "refunded": refunded, "net": received - refunded,
        "payments": payments.select_related("order__patient", "created_by"),
    })


@require_POST
@staff_perm("lab.reverse_labpayment")
def payment_reverse(request, pk):
    payment = get_object_or_404(LabPayment, pk=pk)
    try:
        payment.reverse(request.user, request.POST.get("reason", ""))
    except ValidationError as exc:
        _errors(request, exc)
    else:
        messages.success(request, f"دریافت {payment.number} برگشت خورد.")
    return redirect("lab:staff_order", pk=payment.order_id)

"""دریافت اینترنتی جواب با کد یک‌بارمصرف، آرشیو جواب‌ها در حساب کاربری، نمودار روند و استعلام اصالت برگه."""

import jdatetime
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.views.decorators.cache import never_cache

from apps.audit import log
from apps.audit.context import client_ip
from apps.audit.documents import DocStatus
from apps.audit.models import AuditLog
from apps.core.validators import normalize_digits

from ..models import AccessCode, LabOrder, LabPatient, LabTest
from ..services import portal
from ..services.report import build_report, trend, trend_svg

PENDING_KEY = "lab_pending_code"


class LookupForm(forms.Form):
    number = forms.CharField(label="شماره پذیرش", max_length=24, help_text="روی رسید پذیرش؛ مثل LAB-1405-00012")
    identifier = forms.CharField(label="کد ملی (یا شماره گذرنامه)", max_length=30)

    def clean(self):
        cleaned = super().clean()
        cleaned["number"] = normalize_digits(cleaned.get("number", "")).upper()
        cleaned["identifier"] = normalize_digits(cleaned.get("identifier", ""))
        return cleaned


class CodeForm(forms.Form):
    code = forms.CharField(label="کد پیامک‌شده", max_length=6, widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "one-time-code"}))

    def clean_code(self):
        return normalize_digits(self.cleaned_data["code"])


def _no_store(response):
    response["Cache-Control"] = "no-store, private"
    return response


@never_cache
def result_lookup(request):
    form = LookupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if portal.rate_limited(f"lookup:{client_ip(request)}"):
            messages.error(request, "تعداد درخواست‌ها زیاد است؛ یک ساعت دیگر تلاش کنید یا با آزمایشگاه تماس بگیرید.")
            return redirect("lab:result_lookup")
        order = portal.find_order(form.cleaned_data["number"], form.cleaned_data["identifier"])
        request.session[PENDING_KEY] = None
        if order is not None:
            access = portal.issue_code(order.patient, AccessCode.Purpose.RESULT, order, client_ip(request))
            request.session[PENDING_KEY] = access.pk
        # پیام یکسان تا وجود یا نبود پذیرش افشا نشود.
        messages.info(request, "اگر اطلاعات درست باشد، کد یک‌بارمصرف به موبایل ثبت‌شده در پذیرش پیامک شد.")
        return redirect("lab:result_verify")
    return _no_store(render(request, "lab/portal/lookup.html", {"form": form}))


@never_cache
def result_verify(request):
    form = CodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        pk = request.session.get(PENDING_KEY)
        access = AccessCode.objects.select_related("order").filter(pk=pk, purpose=AccessCode.Purpose.RESULT).first() if pk else None
        try:
            portal.check_code(access, form.cleaned_data["code"])
        except ValidationError as exc:
            form.add_error("code", " ".join(exc.messages))
        else:
            request.session.pop(PENDING_KEY, None)
            portal.grant(request, access.order)
            return redirect("lab:result_view", pk=access.order_id)
    return _no_store(render(request, "lab/portal/verify.html", {"form": form}))


@never_cache
def result_view(request, pk):
    order = get_object_or_404(LabOrder.objects.select_related("patient"), pk=pk, status=DocStatus.POSTED)
    if not portal.can_view(request, order):
        return redirect("lab:result_lookup")
    log.record(AuditLog.Action.EVENT, order, message="مشاهده جواب توسط بیمار")
    back = reverse("lab:my_results") if request.user.is_authenticated and order.patient.user_id == request.user.pk else reverse("lab:result_lookup")
    return _no_store(render(request, "lab/report.html", {**build_report(order, online=True), "back_url": back}))


@login_required
@never_cache
def my_results(request):
    patient = LabPatient.objects.filter(user=request.user).first()
    if patient is None:
        return redirect("lab:link_patient")
    orders = patient.orders.filter(status=DocStatus.POSTED).order_by("-date", "-id")
    tests = LabTest.objects.filter(
        order_items__order__patient=patient, order_items__result__status="approved", result_type="numeric", is_sensitive=False
    ).distinct()
    return _no_store(render(request, "lab/portal/my_results.html", {"patient": patient, "orders": orders, "tests": tests}))


@login_required
@never_cache
def my_trend(request, code):
    patient = get_object_or_404(LabPatient, user=request.user)
    test = get_object_or_404(LabTest, code=code, is_sensitive=False)
    results = trend(patient, test)
    points = [(jdatetime.date.fromgregorian(date=r.item.order.date).strftime("%y/%m/%d"), r.numeric_value) for r in results]
    rr = test.reference_range_for(patient.sex, patient.age_days())
    chart = mark_safe(trend_svg(points, low=rr.low if rr else None, high=rr.high if rr else None))
    log.record(AuditLog.Action.EVENT, patient, message=f"مشاهده نمودار روند {test.code} توسط بیمار")
    return _no_store(render(request, "lab/portal/trend.html", {"test": test, "results": results[::-1], "chart": chart, "rr": rr}))


class LinkForm(forms.Form):
    identifier = forms.CharField(label="کد ملی (یا شماره گذرنامه)", max_length=30)

    def clean_identifier(self):
        return normalize_digits(self.cleaned_data["identifier"])


@login_required
@never_cache
def link_patient(request):
    """اتصال پرونده آزمایشگاه به حساب کاربر: کد به موبایلی که در پذیرش ثبت شده ارسال می‌شود (نه موبایل حساب)."""
    step = "code" if request.session.get("lab_link_code") else "identifier"
    if request.method == "POST" and "identifier" in request.POST:
        form = LinkForm(request.POST)
        if form.is_valid():
            if portal.rate_limited(f"link:{request.user.pk}", limit=5):
                messages.error(request, "تعداد درخواست‌ها زیاد است؛ بعداً تلاش کنید.")
                return redirect("lab:link_patient")
            value = form.cleaned_data["identifier"]
            patient = LabPatient.objects.filter(national_code=value).first() or LabPatient.objects.filter(foreign_id=value).first()
            request.session["lab_link_code"] = None
            if patient:
                access = portal.issue_code(patient, AccessCode.Purpose.LINK, ip=client_ip(request))
                request.session["lab_link_code"] = access.pk
            messages.info(request, "اگر پرونده‌ای با این مشخصات باشد، کد به موبایل ثبت‌شده در آزمایشگاه پیامک شد.")
            return redirect("lab:link_patient")
        step = "identifier"
    elif request.method == "POST":
        form = CodeForm(request.POST)
        if form.is_valid():
            pk = request.session.get("lab_link_code")
            access = AccessCode.objects.select_related("patient").filter(pk=pk, purpose=AccessCode.Purpose.LINK).first() if pk else None
            try:
                portal.check_code(access, form.cleaned_data["code"])
                portal.link_patient(request.user, access.patient)
            except ValidationError as exc:
                form.add_error("code", " ".join(exc.messages))
            else:
                request.session.pop("lab_link_code", None)
                log.record(AuditLog.Action.EVENT, access.patient, message="اتصال پرونده آزمایشگاه به حساب کاربری")
                messages.success(request, "پرونده آزمایشگاه به حساب شما متصل شد.")
                return redirect("lab:my_results")
        step = "code"
    else:
        form = CodeForm() if step == "code" else LinkForm()
    return _no_store(render(request, "lab/portal/link.html", {"form": form, "step": step}))


class VerifyForm(forms.Form):
    number = forms.CharField(label="شماره پذیرش", max_length=24)
    report_code = forms.CharField(label="کد اصالت", max_length=12)


def verify_report(request):
    """استعلام اصالت برگه جواب (برای پزشک، بیمه یا محل کار). فقط حداقل اطلاعات نمایش داده می‌شود."""
    form = VerifyForm(request.POST or None)
    found = None
    if request.method == "POST" and form.is_valid():
        if portal.rate_limited(f"verify:{client_ip(request)}"):
            messages.error(request, "تعداد درخواست‌ها زیاد است؛ بعداً تلاش کنید.")
        else:
            order = LabOrder.objects.select_related("patient").filter(
                number__iexact=normalize_digits(form.cleaned_data["number"]).strip(),
                report_code=normalize_digits(form.cleaned_data["report_code"]).strip().upper(),
            ).first()
            found = {"order": order, "report": build_report(order) if order else None}
    return render(request, "lab/portal/verify_report.html", {"form": form, "found": found})

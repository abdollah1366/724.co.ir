"""پرونده سلامت من (پورتال بیمار)."""

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache

from apps.audit import log
from apps.audit.context import client_ip
from apps.audit.models import AuditLog
from apps.core.validators import normalize_digits

from . import portal
from .models import DocumentFile, PortalCode


class IdentifierForm(forms.Form):
    identifier = forms.CharField(label="کد ملی (یا شماره گذرنامه)", max_length=30)

    def clean_identifier(self):
        return normalize_digits(self.cleaned_data["identifier"]).strip()


class CodeForm(forms.Form):
    code = forms.CharField(label="کد پیامک‌شده", max_length=6)

    def clean_code(self):
        return normalize_digits(self.cleaned_data["code"])


SESSION_KEY = "ehr_portal_code"


def _no_store(response):
    response["Cache-Control"] = "no-store, private"
    return response


@login_required
@never_cache
def my_record(request):
    patient = portal.patient_for(request.user)
    if patient is None:
        return redirect("ehr:portal_link")
    log.record(AuditLog.Action.EVENT, patient, message="مشاهده پرونده سلامت توسط بیمار")
    return _no_store(render(request, "ehr/portal.html", {"patient": patient, **portal.summary(patient)}))


@login_required
@never_cache
def portal_link(request):
    """کد به موبایلی که در پذیرش درمانگاه یا آزمایشگاه ثبت و تأیید شده فرستاده می‌شود، نه موبایل حساب کاربری."""
    if portal.patient_for(request.user):
        return redirect("ehr:my_record")
    step = "code" if request.session.get(SESSION_KEY) else "identifier"
    form = CodeForm() if step == "code" else IdentifierForm()
    if request.method == "POST" and "identifier" in request.POST:
        form = IdentifierForm(request.POST)
        if form.is_valid():
            if portal.rate_limited(f"ehr-link:{request.user.pk}", limit=5):
                messages.error(request, "تعداد درخواست‌ها زیاد است؛ بعداً تلاش کنید.")
                return redirect("ehr:portal_link")
            patient = portal.find(form.cleaned_data["identifier"])
            access = portal.issue_code(patient, request.user, client_ip(request)) if patient else None
            request.session[SESSION_KEY] = access.pk if access else -1
            # پاسخ یکسان تا وجود یا نبود پرونده برای دیگران معلوم نشود
            messages.info(request, "اگر پرونده تأییدشده‌ای با این مشخصات باشد، کد به موبایل ثبت‌شده در پذیرش پیامک شد.")
            return redirect("ehr:portal_link")
        step = "identifier"
    elif request.method == "POST":
        form = CodeForm(request.POST)
        if form.is_valid():
            access = PortalCode.objects.select_related("patient").filter(pk=request.session.get(SESSION_KEY)).first()
            try:
                patient = portal.verify_and_link(access, request.user, form.cleaned_data["code"])
            except ValidationError as exc:
                form.add_error("code", " ".join(exc.messages))
            else:
                request.session.pop(SESSION_KEY, None)
                log.record(AuditLog.Action.EVENT, patient, message="اتصال پرونده سلامت به حساب کاربری")
                messages.success(request, "پرونده سلامت به حساب شما متصل شد.")
                return redirect("ehr:my_record")
        step = "code"
    if request.GET.get("restart"):
        request.session.pop(SESSION_KEY, None)
        return redirect("ehr:portal_link")
    return _no_store(render(request, "ehr/portal_link.html", {"form": form, "step": step}))


@login_required
@never_cache
def my_document_file(request, pk):
    item = get_object_or_404(DocumentFile.objects.select_related("document__patient"), pk=pk)
    document = item.document
    if document.is_void or document.patient.user_id != request.user.pk:
        raise Http404
    log.record(AuditLog.Action.EVENT, document.patient, message=f"دریافت فایل مدرک {document.pk} توسط بیمار")
    response = FileResponse(item.file.open("rb"), content_type=item.content_type)
    response["X-Content-Type-Options"] = "nosniff"
    return _no_store(response)

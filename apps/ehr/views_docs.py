"""درخواست آزمایش، روند جواب‌ها و مدارک بیرونی (اسکن و تصویر) در پرونده."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import clinical, labs, services
from .forms import ExternalDocumentForm, ExternalValueFormSet, LabRequestForm, ReasonForm
from .models import DocumentFile, Encounter, ExternalDocument, LabRequest, Patient
from .views import _errors, _require_access, staff_perm


@staff_perm("ehr.add_labrequest")
def lab_request_new(request, pk):
    encounter = get_object_or_404(Encounter.objects.select_related("patient"), pk=pk)
    if not clinical.can_write(request.user, encounter):
        raise PermissionDenied
    form = LabRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            lab_request = labs.create_request(
                encounter.patient, encounter.provider, request.user, form.cleaned_data["tests"], form.cleaned_data["panels"],
                encounter, form.cleaned_data["is_urgent"], form.cleaned_data["clinical_note"])
            messages.success(request, f"درخواست آزمایش {lab_request.number} ثبت شد و در پذیرش آزمایشگاه دیده می‌شود.")
            return redirect("ehr:encounter", pk=encounter.pk)
        except ValidationError as exc:
            _errors(request, exc)
    return render(request, "ehr/lab_request_form.html", {"form": form, "encounter": encounter, "patient": encounter.patient})


@staff_perm("lab.add_laborder")
def lab_request_queue(request):
    """پذیرش آزمایشگاه: درخواست‌های باز پزشکان درمانگاه (فقط نام آزمایش‌ها، نه پرونده)."""
    if request.method == "POST":
        lab_request = get_object_or_404(LabRequest, pk=request.POST.get("request"))
        try:
            order = labs.receive_request(lab_request, request.user)
            messages.success(request, "پذیرش پیش‌نویس ساخته شد؛ بیمه و پرداخت را تکمیل کنید.")
            return redirect("lab:staff_reception_edit", pk=order.pk)
        except ValidationError as exc:
            _errors(request, exc)
    query = request.GET.get("q", "").strip()
    requests = LabRequest.objects.filter(status=LabRequest.Status.OPEN).select_related("patient", "doctor").prefetch_related(
        "tests", "panels")
    if query:
        requests = requests.filter(patient__national_code=query) | requests.filter(number__iexact=query)
    return render(request, "ehr/lab_request_queue.html", {"requests": requests[:100], "query": query})


@staff_perm("ehr.view_clinical_record")
def patient_labs(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    kind = _require_access(request, patient)
    services.log_access(request, patient, kind, "روند آزمایش‌ها")
    history = labs.lab_history(patient)
    rows = []
    for test, points in history.items():
        numeric = [clinical.Reading(p.when, p.numeric, p.value) for p in points if p.numeric is not None]
        rows.append({"test": test, "points": points, "spark": clinical.sparkline(numeric)})
    return render(request, "ehr/patient_labs.html", {
        "patient": patient, "rows": rows,
        "requests": patient.lab_requests.select_related("doctor", "lab_order").prefetch_related("tests", "panels")[:20],
    })


@staff_perm("ehr.add_externaldocument")
def document_upload(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    _require_access(request, patient)
    form = ExternalDocumentForm(request.POST or None, request.FILES or None)
    values = ExternalValueFormSet(request.POST or None, prefix="val")
    if request.method == "POST" and form.is_valid() and values.is_valid():
        with transaction.atomic():
            document = form.save(commit=False)
            document.patient, document.uploaded_by = patient, request.user
            encounter = patient.encounters.filter(status=Encounter.Status.OPEN, provider__user=request.user).first()
            document.encounter = encounter
            document.save()
            for page, upload in enumerate(form.cleaned_data["files"], start=1):
                DocumentFile.objects.create(document=document, file=upload, page=page)
            for value_form in values:
                data = value_form.cleaned_data
                if data.get("test") and data.get("value") is not None:
                    document.values.create(test=data["test"], value=data["value"], unit=data.get("unit", ""))
        messages.success(request, "مدرک بارگذاری شد.")
        return redirect("ehr:document", pk=document.pk)
    return render(request, "ehr/document_form.html", {"form": form, "values": values, "patient": patient})


@staff_perm("ehr.view_externaldocument")
def document(request, pk):
    document = get_object_or_404(ExternalDocument.objects.select_related("patient", "uploaded_by"), pk=pk)
    kind = _require_access(request, document.patient)
    services.log_access(request, document.patient, kind, f"مدرک {document.pk}")
    return render(request, "ehr/document.html", {
        "document": document, "patient": document.patient, "files": document.files.all(),
        "values": document.values.select_related("test"),
        "void_form": ReasonForm() if request.user.has_perm("ehr.change_externaldocument") and not document.is_void else None,
    })


@staff_perm("ehr.view_externaldocument")
def document_file(request, pk):
    """سرو فایل خصوصی فقط با مجوز؛ هر بار باز شدن ثبت می‌شود."""
    item = get_object_or_404(DocumentFile.objects.select_related("document__patient"), pk=pk)
    patient = item.document.patient
    kind = services.access_kind(request, patient)
    if kind is None:
        raise PermissionDenied
    services.log_access(request, patient, kind, f"فایل مدرک {item.document_id}")
    try:
        handle = item.file.open("rb")
    except FileNotFoundError:
        raise Http404("فایل پیدا نشد.")
    response = FileResponse(handle, content_type=item.content_type)
    extension = item.file.name.rsplit(".", 1)[-1]
    response["Content-Disposition"] = f'inline; filename="document-{item.document_id}-{item.page}.{extension}"'
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@staff_perm("ehr.change_externaldocument")
@require_POST
def document_void(request, pk):
    document = get_object_or_404(ExternalDocument, pk=pk)
    _require_access(request, document.patient)
    form = ReasonForm(request.POST)
    if form.is_valid():
        ExternalDocument.objects.filter(pk=document.pk).update(is_void=True, void_reason=form.cleaned_data["reason"])
        messages.success(request, "مدرک «اشتباه ثبت شده» علامت خورد (حذف نمی‌شود).")
    return redirect("ehr:document", pk=document.pk)

"""نسخه‌نویسی پزشک، اطلاعات دارو و موجودی داروخانه، و میز تحویل نسخه داروساز."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.validators import normalize_digits
from apps.shop.models import Product, ProductKind

from . import pharmacy, services
from .forms import CounselingForm, PrescriptionItemForm, PrescriptionMetaForm, ReasonForm
from .models import DrugCounseling, Encounter, GenericDrug, Patient, Prescription, PrescriptionItem
from .views import _errors, _require_access, staff_perm


def _own(request, prescription):
    doctor = services.provider_for(request.user)
    return doctor is not None and doctor.pk == prescription.prescriber_id


@staff_perm("ehr.add_prescription")
@require_POST
def prescription_new(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    _require_access(request, patient)
    doctor = services.provider_for(request.user)
    if doctor is None:
        messages.error(request, "حساب شما به پزشکی وصل نیست.")
        return redirect("ehr:chart", pk=patient.pk)
    encounter = None
    if request.POST.get("encounter"):
        encounter = get_object_or_404(Encounter, pk=request.POST["encounter"], patient=patient, provider=doctor)
    draft = Prescription.objects.filter(patient=patient, prescriber=doctor, status=Prescription.Status.DRAFT,
                                        encounter=encounter).first()
    prescription = draft or Prescription.objects.create(patient=patient, prescriber=doctor, encounter=encounter,
                                                        created_by=request.user)
    return redirect("ehr:prescription", pk=prescription.pk)


@staff_perm("ehr.view_prescription")
def prescription(request, pk):
    prescription = get_object_or_404(Prescription.objects.select_related("patient", "prescriber"), pk=pk)
    patient = prescription.patient
    _require_access(request, patient)
    editable = prescription.is_draft and _own(request, prescription)
    item_form = PrescriptionItemForm()
    meta_form = PrescriptionMetaForm(instance=prescription, prefix="m") if editable else None
    if request.method == "POST":
        if not editable:
            raise PermissionDenied
        action = request.POST.get("action")
        if action == "add":
            item_form = PrescriptionItemForm(request.POST)
            if item_form.is_valid():
                data = item_form.cleaned_data
                drug = data["drug"]
                warnings = pharmacy.alerts(patient, drug.get("generic"), drug.get("product"), exclude_prescription=prescription)
                if any(level == "danger" for level, _ in warnings) and not data["override"]:
                    for _, text in warnings:
                        messages.error(request, text)
                    messages.error(request, "برای ثبت با وجود حساسیت، گزینه تأیید هشدار را بزنید.")
                else:
                    pharmacy.add_item(prescription, generic=drug.get("generic"), product=drug.get("product"),
                                      quantity=data["quantity"], dose=data["dose"], frequency=data["frequency"],
                                      duration_days=data["duration_days"], instructions=data["instructions"])
                    for level, text in warnings:
                        (messages.error if level == "danger" else messages.warning)(request, text)
                    return redirect("ehr:prescription", pk=prescription.pk)
        elif action == "issue":
            meta_form = PrescriptionMetaForm(request.POST, instance=prescription, prefix="m")
            if meta_form.is_valid():
                try:
                    meta_form.save()
                    prescription.issue(request.user)
                    messages.success(request, f"نسخه {prescription.number} صادر و برای داروخانه درمانگاه ارسال شد.")
                    return redirect("ehr:prescription", pk=prescription.pk)
                except ValidationError as exc:
                    _errors(request, exc)
        elif action == "delete_item":
            get_object_or_404(PrescriptionItem, pk=request.POST.get("item"), prescription=prescription).delete()
            return redirect("ehr:prescription", pk=prescription.pk)
    items = []
    for item in prescription.items.select_related("generic", "product"):
        items.append({
            "item": item, "stock": pharmacy.stock_for(item.generic, item.product),
            "alerts": pharmacy.alerts(patient, item.generic, item.product, exclude_prescription=prescription) if editable else [],
        })
    return render(request, "ehr/prescription.html", {
        "prescription": prescription, "patient": patient, "items": items, "editable": editable, "item_form": item_form,
        "meta_form": meta_form, "cancel_form": ReasonForm() if _own(request, prescription) else None,
        "current_meds": pharmacy.current_medications(patient),
        "allergies": patient.allergies.filter(is_active=True),
    })


@staff_perm("ehr.change_prescription")
@require_POST
def prescription_cancel(request, pk):
    prescription = get_object_or_404(Prescription, pk=pk)
    if not _own(request, prescription):
        raise PermissionDenied
    form = ReasonForm(request.POST)
    if form.is_valid():
        try:
            prescription.cancel(form.cleaned_data["reason"])
            messages.success(request, "نسخه لغو شد.")
        except ValidationError as exc:
            _errors(request, exc)
    return redirect("ehr:prescription", pk=prescription.pk)


@staff_perm("ehr.view_prescription")
def prescription_print(request, pk):
    prescription = get_object_or_404(Prescription.objects.select_related("patient", "prescriber"), pk=pk,
                                     status=Prescription.Status.ISSUED)
    if not (request.user.has_perm("ehr.dispense_prescription") or services.access_kind(request, prescription.patient)):
        raise PermissionDenied
    return render(request, "ehr/prescription_print.html", {"prescription": prescription,
                                                          "items": prescription.items.select_related("generic", "product")})


@staff_perm("ehr.add_prescription")
def drug_search(request):
    """ژنریک‌ها و کالاهای داروخانه (دارو و تجهیزات) با موجودی."""
    query = normalize_digits(request.GET.get("q", "")).strip()
    results = []
    if len(query) >= 2:
        for g in GenericDrug.objects.filter(is_active=True).filter(Q(name__icontains=query) | Q(name_fa__icontains=query))[:15]:
            stock = sum(p.stock for p in g.products.filter(is_active=True))
            results.append({"value": f"G{g.pk}", "label": f"{g} · ژنریک · موجودی داروخانه: {stock}"})
        products = Product.objects.filter(is_active=True, kind__in=[ProductKind.DRUG, ProductKind.EQUIPMENT]).filter(
            Q(name__icontains=query) | Q(generic_name__icontains=query) | Q(brand__icontains=query))[:15]
        for p in products:
            results.append({"value": f"P{p.pk}", "label": f"{p.name} · {p.get_kind_display()} · موجودی: {p.stock}"})
    return JsonResponse({"results": results})


@staff_perm("ehr.view_genericdrug")
def drug_info(request, kind, pk):
    """اطلاعات علمی دارو از داروخانه درمانگاه برای مشاوره پزشک با بیمار."""
    if kind == "g":
        generic = get_object_or_404(GenericDrug, pk=pk)
        products = list(generic.products.filter(is_active=True))
    else:
        product = get_object_or_404(Product, pk=pk)
        generic, products = product.generic, [product]
    return render(request, "ehr/drug_info.html", {"generic": generic, "products": products})


# ---------------------------------------------------------------- داروخانه


@staff_perm("ehr.dispense_prescription")
def pharmacy_queue(request):
    query = normalize_digits(request.GET.get("q", "")).strip()
    prescriptions = Prescription.objects.filter(status=Prescription.Status.ISSUED).select_related("patient", "prescriber")
    if query:
        prescriptions = prescriptions.filter(Q(number__iexact=query) | Q(patient__national_code=query) |
                                             Q(patient__file_number__iexact=query))
    rows = [p for p in prescriptions[:100] if query or p.dispense_status in ("تحویل نشده", "تحویل ناقص")]
    return render(request, "ehr/pharmacy_queue.html", {"rows": rows, "query": query})


@staff_perm("ehr.dispense_prescription")
def pharmacy_prescription(request, pk):
    """داروساز فقط نسخه را می‌بیند (نه کل پرونده): دارو، دستور مصرف، حساسیت‌ها و موجودی."""
    prescription = get_object_or_404(Prescription.objects.select_related("patient", "prescriber"), pk=pk,
                                     status=Prescription.Status.ISSUED)
    items = list(prescription.items.select_related("generic", "product"))
    if request.method == "POST" and request.POST.get("action") == "dispense":
        selections = []
        try:
            for item in items:
                product_id = request.POST.get(f"product-{item.pk}")
                quantity = int(normalize_digits(request.POST.get(f"qty-{item.pk}") or "0") or 0)
                if not quantity:
                    continue
                product = get_object_or_404(Product, pk=product_id)
                selections.append((item, product, quantity))
            pharmacy.dispense(prescription, request.user, selections)
            messages.success(request, "داروها به فاکتور صندوق اضافه شد؛ پرداخت را در صندوق ثبت کنید.")
            return redirect("backoffice:pos")
        except (ValidationError, ValueError) as exc:
            _errors(request, exc)
    counseling_form = CounselingForm(request.POST if request.POST.get("action") == "counsel" else None, prefix="c")
    if request.method == "POST" and request.POST.get("action") == "counsel":
        if not request.user.has_perm("ehr.add_drugcounseling"):
            raise PermissionDenied
        if counseling_form.is_valid():
            counseling = counseling_form.save(commit=False)
            counseling.patient, counseling.prescription, counseling.user = prescription.patient, prescription, request.user
            counseling.save()
            messages.success(request, "مشاوره دارویی ثبت شد.")
            return redirect("ehr:pharmacy_prescription", pk=prescription.pk)
    rows = [{"item": item, "products": [p for p in item.candidate_products() if p.is_sellable]} for item in items]
    return render(request, "ehr/pharmacy_prescription.html", {
        "prescription": prescription, "patient": prescription.patient, "rows": rows,
        "allergies": prescription.patient.allergies.filter(is_active=True, kind="drug"),
        "counseling_form": counseling_form, "counselings": prescription.counselings.select_related("user"),
    })


@staff_perm("ehr.add_drugcounseling")
@require_POST
def chart_counseling(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    _require_access(request, patient)
    form = CounselingForm(request.POST, prefix="c")
    if form.is_valid():
        DrugCounseling.objects.create(patient=patient, user=request.user, **form.cleaned_data)
        messages.success(request, "مشاوره دارویی ثبت شد.")
    return redirect("ehr:chart", pk=patient.pk)

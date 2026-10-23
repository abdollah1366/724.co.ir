"""پرونده سلامت برای کادر درمان: جست‌وجوی بیمار، پرونده یکپارچه، ثبت دلیل دسترسی و گزارش مشاهده‌ها."""

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.appointments.models import Appointment
from apps.core.validators import normalize_digits

from . import clinical, pharmacy, services
from .forms import (
    AddendumForm, AllergyForm, CounselingForm, DiagnosisForm, EmergencyAccessForm, EncounterForm, EncounterServiceForm, HistoryForm, PatientForm, ProblemForm,
    ReferralForm, VitalsForm,
)
from .models import (
    Allergy, ChronicProgram, Diagnosis, Encounter, EncounterService, ICD10Code, Patient, Problem, RecordAccess, ServiceCode,
)


def staff_perm(perm):
    def decorator(view):
        return staff_member_required(permission_required(perm, raise_exception=True)(view))

    return decorator


@staff_perm("ehr.view_patient")
def patient_search(request):
    query = request.GET.get("q", "")
    return render(request, "ehr/search.html", {"query": query, "patients": services.search(query)})


@staff_perm("ehr.view_clinical_record")
def my_patients(request):
    doctor = services.provider_for(request.user)
    today = timezone.localdate()
    appointments = []
    if doctor:
        appointments = (
            Appointment.objects.active().filter(doctor=doctor, date=today).select_related("patient", "encounter").order_by("time")
        )
    return render(request, "ehr/my_patients.html", {"doctor": doctor, "appointments": appointments, "today": today})


@staff_member_required
def patient_edit(request, pk=None):
    patient = get_object_or_404(Patient, pk=pk) if pk else None
    if not request.user.has_perm("ehr.change_patient" if patient else "ehr.add_patient"):
        raise PermissionDenied
    form = PatientForm(request.POST or None, instance=patient)
    if request.method == "POST" and form.is_valid():
        patient = form.save(commit=False)
        patient.is_verified = True
        patient.save()
        messages.success(request, f"پرونده {patient.file_number} ذخیره شد.")
        if request.user.has_perm("ehr.view_clinical_record"):
            return redirect("ehr:chart", pk=patient.pk)
        return redirect(f"{reverse('ehr:search')}?q={patient.file_number}")
    return render(request, "ehr/patient_form.html", {"form": form, "patient": patient})


@staff_perm("ehr.view_clinical_record")
def chart(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    kind = services.access_kind(request, patient)
    if kind is None:
        form = EmergencyAccessForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            services.grant_emergency(request, patient, form.cleaned_data["reason"])
            return redirect("ehr:chart", pk=patient.pk)
        # پیش از ثبت دلیل فقط نام و شماره پرونده دیده می‌شود.
        return render(request, "ehr/access_reason.html", {"patient": patient, "form": form,
                                                          "hours": services.EMERGENCY_GRANT_HOURS})
    if kind != RecordAccess.Kind.EMERGENCY:
        services.log_access(request, patient, kind)
    selected = request.GET.get("kind", "")
    selected = selected if selected in services.KINDS else ""
    events = services.timeline(patient, selected or None, request.user)
    summary = clinical.chronic_summary(patient)
    return render(request, "ehr/chart.html", {
        "patient": patient, "events": events, "kinds": services.KINDS, "selected": selected, "access_kind": kind,
        "lab_patient": getattr(patient, "lab_patient", None), "summary": summary,
        "problems": patient.problems.select_related("icd"), "allergies": patient.allergies.all(),
        "history": patient.history.all(), "open_encounter": patient.encounters.filter(status=Encounter.Status.OPEN).first(),
        "referrals": patient.referrals.filter(status="open").select_related("to_provider", "from_provider"),
        "sparks": {key: clinical.sparkline(summary[key]) for key in ("bp", "weight", "hba1c", "glucose")},
        "problem_form": ProblemForm(prefix="problem"), "allergy_form": AllergyForm(prefix="allergy"),
        "history_form": HistoryForm(prefix="history"), "is_provider": services.provider_for(request.user) is not None,
        "current_meds": pharmacy.current_medications(patient), "counseling_form": CounselingForm(prefix="c"),
        "provider": services.provider_for(request.user),
        "plans": patient.treatment_plans.filter(status="active").select_related("therapist"),
    })


@staff_perm("ehr.view_recordaccess")
def access_log(request):
    entries = RecordAccess.objects.select_related("patient", "user")
    if request.GET.get("emergency"):
        entries = entries.filter(kind=RecordAccess.Kind.EMERGENCY)
    if request.GET.get("patient"):
        entries = entries.filter(patient__file_number=request.GET["patient"].strip())
    page = Paginator(entries, 50).get_page(request.GET.get("page"))
    return render(request, "ehr/access_log.html", {"page": page})


# ---------------------------------------------------------------- ویزیت و سوابق بالینی


def _require_access(request, patient):
    kind = services.access_kind(request, patient)
    if kind is None:
        raise PermissionDenied
    return kind


def _errors(request, exc):
    messages.error(request, " ".join(exc.messages) if hasattr(exc, "messages") else str(exc))


@staff_perm("ehr.add_encounter")
@require_POST
def encounter_start(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    appointment = None
    if request.POST.get("appointment"):
        appointment = get_object_or_404(Appointment, pk=request.POST["appointment"], patient=patient)
    _require_access(request, patient)
    try:
        encounter = clinical.start_encounter(patient, services.provider_for(request.user), request.user, appointment,
                                             request.POST.get("kind") or Encounter.Kind.VISIT)
    except ValidationError as exc:
        _errors(request, exc)
        return redirect("ehr:chart", pk=patient.pk)
    return redirect("ehr:encounter", pk=encounter.pk)


@staff_perm("ehr.view_encounter")
def encounter(request, pk):
    encounter = get_object_or_404(Encounter.objects.select_related("patient", "provider"), pk=pk)
    patient = encounter.patient
    kind = _require_access(request, patient)
    writable = clinical.can_write(request.user, encounter)
    form = EncounterForm(request.POST or None, instance=encounter, prefix="e") if writable else None
    if request.method == "POST":
        if not writable:
            raise PermissionDenied
        if form.is_valid():
            form.save()
            messages.success(request, "شرح حال ذخیره شد.")
            return redirect("ehr:encounter", pk=encounter.pk)
    else:
        services.log_access(request, patient, kind, f"ویزیت {encounter.pk}")
    return render(request, "ehr/encounter.html", {
        "encounter": encounter, "patient": patient, "form": form, "writable": writable,
        "vitals_form": VitalsForm(prefix="v") if encounter.is_open else None,
        "diagnosis_form": DiagnosisForm(prefix="d") if writable else None,
        "referral_form": ReferralForm(prefix="r") if writable else None,
        "service_form": EncounterServiceForm(prefix="s", initial={"sector": settings.EHR_TARIFF_SECTOR}) if writable else None,
        "services": encounter.services.select_related("service_code"),
        "prescriptions": encounter.prescriptions.prefetch_related("items__generic", "items__product"),
        "lab_requests": encounter.lab_requests.prefetch_related("tests"),
        "addendum_form": AddendumForm() if not encounter.is_open and getattr(services.provider_for(request.user), "pk", None) == encounter.provider_id else None,
        "vitals": encounter.vitals.all(), "diagnoses": encounter.diagnoses.select_related("icd"),
        "allergies": patient.allergies.filter(is_active=True), "problems": patient.problems.filter(status="active"),
        "summary": clinical.chronic_summary(patient),
    })


@staff_perm("ehr.add_vitalsigns")
@require_POST
def encounter_vitals(request, pk):
    encounter = get_object_or_404(Encounter, pk=pk)
    _require_access(request, encounter.patient)
    form = VitalsForm(request.POST, prefix="v")
    if form.is_valid() and encounter.is_open:
        vitals = form.save(commit=False)
        vitals.patient, vitals.encounter, vitals.recorded_by = encounter.patient, encounter, request.user
        vitals.save()
        messages.success(request, "علائم حیاتی ثبت شد.")
    else:
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs) or "ویزیت بسته است.")
    return redirect("ehr:encounter", pk=encounter.pk)


@staff_perm("ehr.add_diagnosis")
@require_POST
def encounter_diagnosis(request, pk):
    encounter = get_object_or_404(Encounter, pk=pk)
    if not clinical.can_write(request.user, encounter):
        raise PermissionDenied
    form = DiagnosisForm(request.POST, prefix="d")
    if form.is_valid():
        diagnosis = form._apply_icd(form.save(commit=False))
        diagnosis.encounter = encounter
        diagnosis.save()
        if request.POST.get("d-add_problem"):
            _problem_from_diagnosis(request, encounter.patient, diagnosis)
    else:
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
    return redirect("ehr:encounter", pk=encounter.pk)


def _problem_from_diagnosis(request, patient, diagnosis):
    exists = patient.problems.filter(status=Problem.Status.ACTIVE)
    exists = exists.filter(icd=diagnosis.icd) if diagnosis.icd_id else exists.filter(title=diagnosis.text)
    if not exists.exists():
        Problem.objects.create(patient=patient, icd=diagnosis.icd, title=diagnosis.text, recorded_by=request.user,
                               chronic_program=CHRONIC_BY_ICD.get(getattr(diagnosis.icd, "code", "")[:3], ""))


# پیشنهاد خودکار برنامه پایش بر اساس سه کاراکتر اول کد
CHRONIC_BY_ICD = {"E10": "diabetes", "E11": "diabetes", "I10": "hypertension", "E78": "dyslipidemia", "J45": "asthma",
                  "J44": "asthma", "E03": "thyroid", "E05": "thyroid"}


@staff_perm("ehr.delete_diagnosis")
@require_POST
def diagnosis_delete(request, pk):
    diagnosis = get_object_or_404(Diagnosis, pk=pk)
    if not clinical.can_write(request.user, diagnosis.encounter):
        raise PermissionDenied
    diagnosis.delete()
    return redirect("ehr:encounter", pk=diagnosis.encounter_id)


@staff_perm("ehr.add_referral")
@require_POST
def encounter_referral(request, pk):
    encounter = get_object_or_404(Encounter, pk=pk)
    if not clinical.can_write(request.user, encounter):
        raise PermissionDenied
    form = ReferralForm(request.POST, prefix="r")
    if form.is_valid():
        referral = form.save(commit=False)
        referral.patient, referral.encounter = encounter.patient, encounter
        referral.from_provider, referral.created_by = encounter.provider, request.user
        referral.save()
        messages.success(request, "ارجاع ثبت شد.")
    else:
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
    return redirect("ehr:encounter", pk=encounter.pk)


@staff_perm("ehr.sign_encounter")
@require_POST
def encounter_sign(request, pk):
    encounter = get_object_or_404(Encounter, pk=pk)
    try:
        clinical.sign(encounter, request.user)
        messages.success(request, "ویزیت امضا و قفل شد.")
    except ValidationError as exc:
        _errors(request, exc)
    return redirect("ehr:encounter", pk=encounter.pk)


@staff_perm("ehr.add_encounteraddendum")
@require_POST
def encounter_addendum(request, pk):
    encounter = get_object_or_404(Encounter, pk=pk)
    form = AddendumForm(request.POST)
    if form.is_valid():
        try:
            clinical.add_addendum(encounter, request.user, form.cleaned_data["text"])
        except ValidationError as exc:
            _errors(request, exc)
    return redirect("ehr:encounter", pk=encounter.pk)


CHART_FORMS = {
    "problem": (ProblemForm, "ehr.add_problem", "مشکل / بیماری ثبت شد."),
    "allergy": (AllergyForm, "ehr.add_allergy", "حساسیت ثبت شد."),
    "history": (HistoryForm, "ehr.add_historyentry", "سابقه ثبت شد."),
}


@staff_perm("ehr.view_clinical_record")
@require_POST
def chart_add(request, pk, what):
    patient = get_object_or_404(Patient, pk=pk)
    if what not in CHART_FORMS:
        raise Http404
    form_class, perm, done = CHART_FORMS[what]
    if not request.user.has_perm(perm):
        raise PermissionDenied
    _require_access(request, patient)
    form = form_class(request.POST, prefix=what)
    if form.is_valid():
        obj = form.save(commit=False)
        if hasattr(form, "_apply_icd"):
            form._apply_icd(obj)
        obj.patient, obj.recorded_by = patient, request.user
        obj.save()
        messages.success(request, done)
    else:
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
    return redirect(reverse("ehr:chart", args=[patient.pk]) + "#summary")


@staff_perm("ehr.change_problem")
@require_POST
def problem_resolve(request, pk):
    problem = get_object_or_404(Problem, pk=pk)
    _require_access(request, problem.patient)
    problem.status = Problem.Status.RESOLVED if problem.status == Problem.Status.ACTIVE else Problem.Status.ACTIVE
    problem.save(update_fields=["status", "updated_at"])
    return redirect(reverse("ehr:chart", args=[problem.patient_id]) + "#summary")


@staff_perm("ehr.change_allergy")
@require_POST
def allergy_toggle(request, pk):
    allergy = get_object_or_404(Allergy, pk=pk)
    _require_access(request, allergy.patient)
    allergy.is_active = not allergy.is_active
    allergy.save(update_fields=["is_active"])
    return redirect(reverse("ehr:chart", args=[allergy.patient_id]) + "#summary")


@staff_member_required
def icd_search(request):
    query = normalize_digits(request.GET.get("q", "")).strip()
    codes = ICD10Code.objects.filter(is_active=True)
    if query:
        codes = codes.filter(Q(code__istartswith=query) | Q(title__icontains=query) | Q(title_fa__icontains=query))
    return JsonResponse({"results": [{"code": c.code, "title": c.title_fa or c.title} for c in codes[:20]]})


@staff_perm("ehr.view_all_records")
def chronic_registry(request):
    program = request.GET.get("program") or ChronicProgram.DIABETES
    if program not in ChronicProgram.values or not program:
        raise Http404
    return render(request, "ehr/registry.html", {
        "rows": clinical.registry(program), "program": program,
        "programs": [c for c in ChronicProgram.choices if c[0]], "targets": clinical,
    })


@staff_perm("ehr.add_encounterservice")
@require_POST
def encounter_service(request, pk):
    encounter = get_object_or_404(Encounter, pk=pk)
    if not clinical.can_write(request.user, encounter):
        raise PermissionDenied
    form = EncounterServiceForm(request.POST, prefix="s")
    if form.is_valid():
        item = form.save(commit=False)
        item.encounter = encounter
        item.unit_tariff = item.service_code.tariff(item.sector)
        item.save()
        if item.unit_tariff is None:
            messages.warning(request, "خدمت ثبت شد ولی ضریب ریالی امسال وارد نشده و تعرفه محاسبه نشد.")
    else:
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
    return redirect("ehr:encounter", pk=encounter.pk)


@staff_perm("ehr.delete_encounterservice")
@require_POST
def encounter_service_delete(request, pk):
    item = get_object_or_404(EncounterService, pk=pk)
    if not clinical.can_write(request.user, item.encounter):
        raise PermissionDenied
    item.delete()
    return redirect("ehr:encounter", pk=item.encounter_id)


@staff_member_required
def service_search(request):
    query = normalize_digits(request.GET.get("q", "")).strip()
    codes = ServiceCode.objects.filter(is_active=True)
    if query:
        codes = codes.filter(Q(code__startswith=query) | Q(title__icontains=query))
    return JsonResponse({"results": [
        {"code": c.code, "title": c.title[:120], "rvu": str(c.total_rvu or ""), "cosmetic": c.is_cosmetic} for c in codes[:20]
    ]})

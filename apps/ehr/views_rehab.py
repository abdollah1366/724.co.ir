"""برنامه‌های درمان توانبخشی و روان: ایجاد برنامه، ثبت و امضای جلسه، ارزیابی و پایان درمان."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import rehab, services
from .forms import AssessmentForm, ClosePlanForm, TherapySessionForm, TreatmentPlanForm
from .models import Patient, TherapySession, TreatmentPlan
from .models.rehab import AUDIOGRAM_FREQUENCIES
from .views import _errors, _require_access, staff_perm


def _form_errors(request, form):
    messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))


@staff_perm("ehr.add_treatmentplan")
def plan_new(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    _require_access(request, patient)
    therapist = services.provider_for(request.user)
    if therapist is None:
        messages.error(request, "حساب شما به درمانگری وصل نیست.")
        return redirect("ehr:chart", pk=patient.pk)
    discipline = rehab.DISCIPLINE_BY_PROVIDER.get(therapist.provider_type, "")
    initial = {"discipline": discipline, "share_with_physicians": rehab.default_share(discipline)}
    referral = patient.referrals.filter(to_provider=therapist, status="open").first()
    if referral:
        initial.update(referral=referral.pk, diagnosis=referral.reason)
    form = TreatmentPlanForm(request.POST or None, patient=patient, initial=initial)
    if request.method == "POST" and form.is_valid():
        data = {k: v for k, v in form.cleaned_data.items() if k != "icd_code"}
        try:
            plan = rehab.create_plan(patient, therapist, request.user, icd=form.cleaned_data.get("icd_code"), **data)
            messages.success(request, "برنامه درمان ثبت شد.")
            return redirect("ehr:plan", pk=plan.pk)
        except ValidationError as exc:
            _errors(request, exc)
    return render(request, "ehr/plan_form.html", {"form": form, "patient": patient})


@staff_perm("ehr.view_treatmentplan")
def plan(request, pk):
    plan = get_object_or_404(TreatmentPlan.objects.select_related("patient", "therapist", "icd", "referral"), pk=pk)
    patient = plan.patient
    kind = _require_access(request, patient)
    services.log_access(request, patient, kind, f"برنامه درمان {plan.pk}")
    can_read = plan.can_read_notes(request.user)
    writable = rehab.can_write(request.user, plan)
    kinds = rehab.ASSESSMENTS_BY_DISCIPLINE.get(plan.discipline)
    return render(request, "ehr/plan.html", {
        "plan": plan, "patient": patient, "can_read": can_read, "writable": writable,
        "sessions": plan.sessions.select_related("service_code", "created_by") if can_read else plan.sessions.only(
            "number", "held_at", "attendance", "signed_at", "plan"),
        "assessments": plan.assessments.select_related("created_by") if can_read else [],
        "session_form": TherapySessionForm(prefix="s") if writable else None,
        "assessment_form": AssessmentForm(prefix="a", kinds=kinds) if writable else None,
        "close_form": ClosePlanForm(prefix="c") if plan.status == "active" and (
            rehab.is_therapist(request.user, plan) or request.user.has_perm("ehr.view_all_records")) else None,
        "progress": rehab.progress(plan) if can_read else [], "frequencies": AUDIOGRAM_FREQUENCIES,
        "is_therapist": rehab.is_therapist(request.user, plan),
        "allergies": patient.allergies.filter(is_active=True),
    })


@staff_perm("ehr.add_therapysession")
@require_POST
def plan_session(request, pk):
    plan = get_object_or_404(TreatmentPlan, pk=pk)
    if not rehab.can_write(request.user, plan):
        raise PermissionDenied
    form = TherapySessionForm(request.POST, prefix="s")
    if form.is_valid():
        session = form.save(commit=False)
        try:
            rehab.add_session(plan, request.user, session)
            if request.POST.get("sign"):
                rehab.sign_session(session, request.user)
                messages.success(request, f"جلسه {session.number} ثبت و امضا شد.")
            else:
                messages.success(request, f"جلسه {session.number} ثبت شد؛ پس از بازبینی امضا کنید.")
            if session.service_code_id and session.unit_tariff is None:
                messages.warning(request, "ضریب ریالی امسال وارد نشده و تعرفه محاسبه نشد.")
        except ValidationError as exc:
            _errors(request, exc)
    else:
        _form_errors(request, form)
    return redirect("ehr:plan", pk=plan.pk)


@staff_perm("ehr.add_therapysession")
@require_POST
def session_sign(request, pk):
    session = get_object_or_404(TherapySession, pk=pk)
    try:
        rehab.sign_session(session, request.user)
        messages.success(request, f"جلسه {session.number} امضا و قفل شد.")
    except ValidationError as exc:
        _errors(request, exc)
    return redirect("ehr:plan", pk=session.plan_id)


@staff_perm("ehr.add_assessment")
@require_POST
def plan_assessment(request, pk):
    plan = get_object_or_404(TreatmentPlan, pk=pk)
    if not rehab.can_write(request.user, plan):
        raise PermissionDenied
    form = AssessmentForm(request.POST, prefix="a", kinds=rehab.ASSESSMENTS_BY_DISCIPLINE.get(plan.discipline))
    if form.is_valid():
        data = form.cleaned_data
        try:
            values = rehab.audiogram_data(form.audiogram_values()) if data["kind"] == "audiogram" else {}
            assessment = rehab.add_assessment(plan, request.user, data["kind"], data["score"], values, data["notes"])
            messages.success(request, f"{assessment.get_kind_display()} ثبت شد. {assessment.summary}")
        except ValidationError as exc:
            _errors(request, exc)
    else:
        _form_errors(request, form)
    return redirect("ehr:plan", pk=plan.pk)


@staff_perm("ehr.change_treatmentplan")
@require_POST
def plan_close(request, pk):
    plan = get_object_or_404(TreatmentPlan, pk=pk)
    form = ClosePlanForm(request.POST, prefix="c")
    if form.is_valid():
        try:
            rehab.close_plan(plan, request.user, form.cleaned_data["status"], form.cleaned_data["summary"])
            messages.success(request, "برنامه درمان بسته شد.")
        except ValidationError as exc:
            _errors(request, exc)
    else:
        _form_errors(request, form)
    return redirect("ehr:plan", pk=plan.pk)


@staff_perm("ehr.view_treatmentplan")
def my_plans(request):
    therapist = services.provider_for(request.user)
    plans = TreatmentPlan.objects.none()
    referrals = []
    if therapist:
        plans = therapist.treatment_plans.filter(status=TreatmentPlan.Status.ACTIVE).select_related("patient")
        referrals = therapist.referrals_received.filter(status="open").select_related("patient", "from_provider")
    return render(request, "ehr/my_plans.html", {"therapist": therapist, "plans": plans, "referrals": referrals})

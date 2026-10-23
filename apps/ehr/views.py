"""پرونده سلامت برای کادر درمان: جست‌وجوی بیمار، پرونده یکپارچه، ثبت دلیل دسترسی و گزارش مشاهده‌ها."""

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.appointments.models import Appointment

from . import services
from .forms import EmergencyAccessForm, PatientForm
from .models import Patient, RecordAccess


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
            Appointment.objects.active().filter(doctor=doctor, date=today).select_related("patient").order_by("time")
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
    events = services.timeline(patient, selected or None)
    return render(request, "ehr/chart.html", {
        "patient": patient, "events": events, "kinds": services.KINDS, "selected": selected, "access_kind": kind,
        "lab_patient": getattr(patient, "lab_patient", None),
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

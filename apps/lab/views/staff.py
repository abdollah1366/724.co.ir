"""پیشخوان آزمایشگاه: پذیرش، دریافت، نوبت‌های امروز و فهرست پذیرش‌ها."""

import uuid

import jdatetime

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.audit import log
from apps.audit.documents import DocStatus
from apps.audit.models import AuditLog
from apps.core.validators import normalize_digits
from apps.ehr.models import Patient

from ..forms import OrderForm, PatientForm, PaymentForm, ReasonForm
from ..models import LabOrder, LabPatient, LabPayment, SamplingAppointment
from ..services import orders as order_service


def staff_perm(perm):
    def decorator(view):
        return staff_member_required(permission_required(perm, raise_exception=True)(view))

    return decorator


def _errors(request, exc):
    messages.error(request, " ".join(exc.messages) if hasattr(exc, "messages") else str(exc))


def find_patients(query):
    query = normalize_digits(query)
    if not query:
        return LabPatient.objects.none()
    return LabPatient.objects.filter(
        Q(national_code=query) | Q(foreign_id__iexact=query) | Q(mobile=query) | Q(last_name__icontains=query)
    )[:20]


def find_health_records(query):
    """پرونده‌های سلامت (درمانگاه، داروخانه) با همین کد ملی یا موبایل که هنوز پرونده آزمایشگاه ندارند."""
    query = normalize_digits(query).strip()
    if not query:
        return Patient.objects.none()
    return Patient.objects.filter(lab_patient__isnull=True).filter(
        Q(national_code=query) | Q(foreign_id__iexact=query) | Q(mobile=query) | Q(file_number__iexact=query)
    )[:10]


@staff_perm("lab.add_laborder")
def reception(request, pk=None):
    """پذیرش جدید یا ویرایش پیش‌نویس. بیمار با کد ملی جست‌وجو و در صورت نبودن ساخته می‌شود."""
    order = get_object_or_404(LabOrder, pk=pk, status=DocStatus.DRAFT) if pk else None
    patient = order.patient if order else None
    appointment = None
    if not order:
        if request.GET.get("patient"):
            patient = get_object_or_404(LabPatient, pk=request.GET["patient"])
        if request.GET.get("appointment"):
            appointment = get_object_or_404(SamplingAppointment.objects.active(), pk=request.GET["appointment"])
            patient = patient or LabPatient.objects.filter(national_code=appointment.national_code).first()
        if request.GET.get("ehr") and not patient:
            # بیماری که در درمانگاه یا داروخانه پرونده دارد: مشخصاتش دوباره تایپ نمی‌شود.
            health_record = get_object_or_404(Patient, pk=request.GET["ehr"])
            patient = getattr(health_record, "lab_patient", None)
    if request.method == "POST":
        patient_form = PatientForm(request.POST, instance=patient, prefix="p")
        order_form = OrderForm(request.POST, instance=order, prefix="o", user=request.user)
        if patient_form.is_valid() and order_form.is_valid():
            try:
                with transaction.atomic():
                    patient = patient_form.save()
                    order = order_form.save(commit=False)
                    order.patient = patient
                    if not order.pk:
                        order.created_by = request.user
                        order.appointment = appointment
                    order.save()
                    order_service.set_order_tests(order, order_form.cleaned_data["tests"], order_form.cleaned_data["panels"])
            except (ValidationError, IntegrityError) as exc:
                _errors(request, exc)
            else:
                return redirect("lab:staff_order", pk=order.pk)
    else:
        initial_patient = {}
        initial_order = {}
        health_record = None
        if not patient:
            if request.GET.get("ehr"):
                health_record = Patient.objects.filter(pk=request.GET["ehr"]).first()
            elif appointment:
                health_record = Patient.objects.filter(national_code=appointment.national_code).first()
        if health_record:
            initial_patient = {
                name: getattr(health_record, name) for name in (
                    "national_code", "foreign_id", "first_name", "last_name", "father_name", "sex", "mobile", "phone", "address")
            }
            if health_record.birth_date:
                initial_patient["birth_date"] = jdatetime.date.fromgregorian(date=health_record.birth_date).strftime("%Y/%m/%d")
        elif appointment and not patient:
            first, _, last = appointment.full_name.partition(" ")
            initial_patient = {
                "national_code": appointment.national_code, "first_name": first, "last_name": last,
                "mobile": appointment.mobile, "address": appointment.address,
            }
        if appointment:
            initial_order = {"tests": list(appointment.tests.values_list("pk", flat=True)), "sampling_place": appointment.kind}
        if patient and not order:
            initial_order.update({
                "basic_plan": patient.basic_plan_id, "basic_insurance_no": patient.basic_insurance_no,
                "supplementary_plan": patient.supplementary_plan_id, "supplementary_insurance_no": patient.supplementary_insurance_no,
            })
        patient_form = PatientForm(instance=patient, prefix="p", initial=initial_patient)
        order_form = OrderForm(instance=order, prefix="o", user=request.user, initial=initial_order)
    query = request.GET.get("q", "")
    return render(request, "lab/staff/reception.html", {
        "patient_form": patient_form, "order_form": order_form, "order": order, "patient": patient,
        "appointment": appointment, "query": query, "matches": find_patients(query) if query else None,
        "health_matches": find_health_records(query) if query else None,
    })


@staff_perm("lab.view_laborder")
def order_detail(request, pk):
    order = get_object_or_404(
        LabOrder.objects.select_related("patient", "basic_plan", "supplementary_plan", "appointment"), pk=pk
    )
    log.record(AuditLog.Action.EVENT, order, message=f"مشاهده پذیرش {order}", user=request.user)
    return render(request, "lab/staff/order_detail.html", {
        "order": order,
        "items": order.items.select_related("test", "panel", "result"),
        "specimens": order.specimens.select_related("specimen_type"),
        "payments": order.payments.all(),
        "payment_form": PaymentForm(initial={"amount": max(order.balance_due, 0)}) if order.status == DocStatus.POSTED else None,
        "token": uuid.uuid4().hex,
        "reason_form": ReasonForm(),
    })


@require_POST
@staff_perm("lab.post_laborder")
def order_post(request, pk):
    order = get_object_or_404(LabOrder, pk=pk)
    try:
        order.post(request.user)
    except ValidationError as exc:
        _errors(request, exc)
    else:
        messages.success(request, f"پذیرش {order.number} ثبت شد.")
    return redirect("lab:staff_order", pk=pk)


@require_POST
@staff_perm("lab.change_laborder")
def order_reprice(request, pk):
    order = get_object_or_404(LabOrder, pk=pk)
    try:
        order_service.reprice(order)
    except ValidationError as exc:
        _errors(request, exc)
    else:
        messages.success(request, "مبالغ دوباره محاسبه شد.")
    return redirect("lab:staff_order", pk=pk)


@require_POST
@staff_perm("lab.reverse_laborder")
def order_reverse(request, pk):
    order = get_object_or_404(LabOrder, pk=pk)
    form = ReasonForm(request.POST)
    if not form.is_valid():
        messages.error(request, "علت ابطال را وارد کنید.")
        return redirect("lab:staff_order", pk=pk)
    try:
        order.reverse(request.user, form.cleaned_data["reason"])
    except ValidationError as exc:
        _errors(request, exc)
    else:
        messages.success(request, f"پذیرش {order.number} باطل و سند حسابداری آن برگشت خورد.")
    return redirect("lab:staff_order", pk=pk)


@require_POST
@staff_perm("lab.post_labpayment")
def order_payment(request, pk):
    order = get_object_or_404(LabOrder, pk=pk)
    form = PaymentForm(request.POST)
    if form.is_valid():
        token = request.POST.get("token") or uuid.uuid4().hex
        if LabPayment.objects.filter(client_token=token).exists():
            messages.info(request, "این دریافت قبلاً ثبت شده است.")
            return redirect("lab:staff_order", pk=pk)
        payment = form.save(commit=False)
        payment.order = order
        payment.created_by = request.user
        payment.client_token = token[:40]
        payment.save()
        try:
            payment.post(request.user)
        except ValidationError as exc:
            payment.delete()
            _errors(request, exc)
        else:
            messages.success(request, f"{payment.get_direction_display()} {payment.amount:,} ریال ثبت شد.")
    else:
        messages.error(request, "اطلاعات پرداخت معتبر نیست.")
    return redirect("lab:staff_order", pk=pk)


@require_POST
@staff_perm("lab.deliver_lab_report")
def order_deliver(request, pk):
    order = get_object_or_404(LabOrder, pk=pk)
    if order.progress != LabOrder.Progress.READY:
        messages.error(request, "همه جواب‌ها هنوز تأیید نشده‌اند.")
    elif order.balance_due > 0:
        messages.error(request, "مانده حساب بیمار تسویه نشده است.")
    else:
        order_service.mark_delivered(order, request.user)
        log.record(AuditLog.Action.EVENT, order, message="تحویل حضوری جواب", user=request.user)
        messages.success(request, "تحویل جواب ثبت شد.")
    return redirect("lab:staff_order", pk=pk)


ORDER_ACTIONS = {
    "post": order_post, "reprice": order_reprice, "reverse": order_reverse, "payment": order_payment, "deliver": order_deliver,
}


def order_action(request, pk, action):
    if action not in ORDER_ACTIONS:
        raise Http404
    return ORDER_ACTIONS[action](request, pk)


@staff_perm("lab.view_laborder")
def receipt(request, pk):
    order = get_object_or_404(LabOrder.objects.select_related("patient"), pk=pk, status=DocStatus.POSTED)
    return render(request, "lab/staff/receipt.html", {
        "order": order, "items": order.items.select_related("test"), "payments": order.payments.filter(status=DocStatus.POSTED),
    })


@staff_perm("lab.view_laborder")
def order_list(request):
    orders = LabOrder.objects.select_related("patient").order_by("-date", "-id")
    query = normalize_digits(request.GET.get("q", ""))
    if query:
        orders = orders.filter(
            Q(number__icontains=query) | Q(patient__national_code=query) | Q(patient__last_name__icontains=query)
            | Q(patient__mobile=query) | Q(specimens__barcode=query)
        ).distinct()
    progress = request.GET.get("progress")
    if progress:
        orders = orders.filter(progress=progress, status=DocStatus.POSTED)
    if request.GET.get("unpaid"):
        orders = [o for o in orders.filter(status=DocStatus.POSTED)[:500] if o.balance_due > 0]
    else:
        orders = orders[:200]
    return render(request, "lab/staff/order_list.html", {
        "orders": orders, "query": query, "progress": progress, "progress_choices": LabOrder.Progress.choices,
    })


@staff_perm("lab.view_samplingappointment")
def appointments(request):
    date = request.GET.get("date")
    try:
        day = timezone.datetime.fromisoformat(date).date() if date else timezone.localdate()
    except ValueError:
        day = timezone.localdate()
    items = SamplingAppointment.objects.filter(date=day).prefetch_related("tests").order_by("time")
    return render(request, "lab/staff/appointments.html", {"appointments": items, "day": day})


@require_POST
@staff_perm("lab.change_samplingappointment")
def appointment_status(request, pk):
    appointment = get_object_or_404(SamplingAppointment, pk=pk)
    status = request.POST.get("status")
    if status in (SamplingAppointment.Status.CONFIRMED, SamplingAppointment.Status.NO_SHOW, SamplingAppointment.Status.CANCELLED):
        appointment.status = status
        appointment.save(update_fields=["status"])
    return redirect(f"{reverse('lab:staff_appointments')}?date={appointment.date.isoformat()}")

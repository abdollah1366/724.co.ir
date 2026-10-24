"""آزمایش در پرونده: درخواست از ویزیت، تبدیل به پذیرش آزمایشگاه و روند جواب‌ها (داخلی و بیرونی)."""

from collections import OrderedDict
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import ExternalLabValue, LabRequest


@dataclass
class LabPoint:
    when: object
    value: str
    numeric: float | None
    flag: str = ""
    source: str = ""


def lab_history(patient):
    """{آزمایش: [نقطه‌ها به ترتیب زمان]} از جواب‌های تأییدشده آزمایشگاه ۷۲۴ و مقادیر وارد‌شده از برگه‌های بیرونی."""
    from apps.lab.models import LabResult

    series = {}
    lab_patient = getattr(patient, "lab_patient", None)
    if lab_patient is not None:
        results = LabResult.objects.filter(item__order__patient=lab_patient, status=LabResult.Status.APPROVED).select_related(
            "item__test", "item__order").order_by("approved_at")
        for r in results:
            series.setdefault(r.item.test, []).append(LabPoint(
                r.approved_at, f"{r.value} {r.unit}".strip(), float(r.numeric_value) if r.numeric_value is not None else None,
                r.get_flag_display() if r.flag else "", f"آزمایشگاه ۷۲۴ · {r.item.order.number}"))
    external = ExternalLabValue.objects.filter(document__patient=patient, document__is_void=False).select_related(
        "test", "document")
    for v in external:
        series.setdefault(v.test, []).append(LabPoint(
            v.document.document_date, f"{v.value.normalize()} {v.unit}".strip(), float(v.value), "",
            f"بیرون · {v.document.source or v.document.title}"))
    for points in series.values():
        points.sort(key=lambda p: (p.when.date() if hasattr(p.when, "date") else p.when))
    return OrderedDict(sorted(series.items(), key=lambda item: item[0].name))


def create_request(patient, doctor, user, tests, panels=(), encounter=None, is_urgent=False, clinical_note=""):
    if not tests and not panels:
        raise ValidationError("حداقل یک آزمایش انتخاب کنید.")
    with transaction.atomic():
        request = LabRequest.objects.create(patient=patient, doctor=doctor, encounter=encounter, is_urgent=is_urgent,
                                            clinical_note=clinical_note, created_by=user)
        request.tests.set(tests)
        request.panels.set(panels)
    return request


def ensure_lab_patient(patient):
    """بیمار آزمایشگاه را از پرونده واحد می‌سازد؛ آزمایشگاه جنس و تاریخ تولد را برای محدوده مرجع لازم دارد."""
    from apps.lab.models import LabPatient

    existing = getattr(patient, "lab_patient", None)
    if existing is not None:
        return existing
    if not patient.sex or not patient.birth_date or not patient.mobile or not patient.last_name:
        raise ValidationError("برای پذیرش آزمایشگاه، نام، جنس، تاریخ تولد و موبایل بیمار را در پرونده کامل کنید.")
    return LabPatient.objects.create(
        patient=patient, national_code=patient.national_code, foreign_id=patient.foreign_id, first_name=patient.first_name,
        last_name=patient.last_name, father_name=patient.father_name, sex=patient.sex, birth_date=patient.birth_date,
        mobile=patient.mobile, phone=patient.phone, address=patient.address,
    )


def receive_request(lab_request, user):
    """پذیرش آزمایشگاه پیش‌نویس با آزمایش‌ها و پزشک درخواست‌کننده؛ بیمه و پرداخت در صفحه پذیرش تکمیل می‌شود."""
    from apps.lab.models import LabOrder
    from apps.lab.services import orders as order_service

    if lab_request.status != LabRequest.Status.OPEN:
        raise ValidationError("این درخواست قبلاً پذیرش یا لغو شده است.")
    with transaction.atomic():
        lab_request = LabRequest.objects.select_for_update().get(pk=lab_request.pk)
        if lab_request.status != LabRequest.Status.OPEN:
            raise ValidationError("این درخواست قبلاً پذیرش شده است.")
        lab_patient = ensure_lab_patient(lab_request.patient)
        order = LabOrder.objects.create(
            patient=lab_patient, created_by=user, priority="urgent" if lab_request.is_urgent else "routine",
            referring_doctor=lab_request.doctor.full_name, referring_council_no=lab_request.doctor.medical_council_number,
            clinical_note=lab_request.clinical_note, basic_plan=lab_patient.basic_plan,
            basic_insurance_no=lab_patient.basic_insurance_no, supplementary_plan=lab_patient.supplementary_plan,
            supplementary_insurance_no=lab_patient.supplementary_insurance_no,
        )
        order_service.set_order_tests(order, list(lab_request.tests.all()), list(lab_request.panels.all()))
        lab_request.lab_order = order
        lab_request.status = LabRequest.Status.RECEIVED
        lab_request.save(update_fields=["lab_order", "status"])
    return order

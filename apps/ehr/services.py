"""اتصال سوابق بخش‌ها به پرونده واحد، کنترل دسترسی و خط زمانی پرونده."""

import datetime
from dataclasses import dataclass, field

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from apps.audit.context import client_ip
from apps.audit.documents import DocStatus

from .models import Patient, RecordAccess

EMERGENCY_GRANT_HOURS = 4
SESSION_KEY = "ehr_grants"


# ---------------------------------------------------------------- اتصال به پرونده


def find_patient(national_code=None, foreign_id=None):
    if national_code:
        return Patient.objects.filter(national_code=national_code).first()
    if foreign_id:
        return Patient.objects.filter(foreign_id=foreign_id).first()
    return None


def get_or_create_patient(national_code=None, foreign_id=None, **defaults):
    """پرونده موجود را برمی‌گرداند یا می‌سازد. اطلاعات پرونده موجود را تغییر نمی‌دهد."""
    national_code = national_code or None
    foreign_id = None if national_code else (foreign_id or None)
    if not national_code and not foreign_id:
        return None
    patient = find_patient(national_code, foreign_id)
    if patient:
        return patient
    try:
        with transaction.atomic():
            return Patient.objects.create(national_code=national_code, foreign_id=foreign_id, **defaults)
    except IntegrityError:
        # ثبت هم‌زمان همین کد ملی از بخش دیگر
        return find_patient(national_code, foreign_id)


def split_full_name(full_name):
    parts = (full_name or "").split()
    if len(parts) < 2:
        return "", " ".join(parts)
    return parts[0], " ".join(parts[1:])


def demographics_from_lab_patient(lab_patient):
    return {
        "first_name": lab_patient.first_name, "last_name": lab_patient.last_name, "father_name": lab_patient.father_name,
        "sex": lab_patient.sex, "birth_date": lab_patient.birth_date, "mobile": lab_patient.mobile,
        "phone": lab_patient.phone, "address": lab_patient.address,
    }


def link_lab_patient(lab_patient):
    """پذیرش آزمایشگاه هویت را با مدرک بررسی می‌کند؛ پرونده تأییدنشده با اطلاعات آن کامل و تأیید می‌شود."""
    if lab_patient.patient_id:
        return lab_patient.patient
    data = demographics_from_lab_patient(lab_patient)
    patient = get_or_create_patient(lab_patient.national_code, lab_patient.foreign_id, is_verified=True, **data)
    if patient and not patient.is_verified:
        for name, value in data.items():
            setattr(patient, name, value)
        patient.is_verified = True
        patient.save()
    return patient


def link_appointment(appointment):
    """نوبت اینترنتی هویت را تأیید نمی‌کند؛ پرونده جدید «تأییدنشده» ساخته می‌شود و پرونده موجود تغییر نمی‌کند."""
    if appointment.patient_id:
        return appointment.patient
    first, last = split_full_name(appointment.patient_full_name)
    return get_or_create_patient(appointment.national_code, first_name=first, last_name=last, mobile=appointment.mobile)


# ---------------------------------------------------------------- دسترسی


def provider_for(user):
    """پزشک متصل به این حساب کاربری (در مراحل بعد درمانگران توانبخشی هم اضافه می‌شوند)."""
    return getattr(user, "doctor", None) if user.is_authenticated else None


def has_care_relationship(user, patient):
    doctor = provider_for(user)
    if doctor is None:
        # پرستار و کادر بالینی غیرپزشک: بیمارانی که امروز در درمانگاه نوبت یا ویزیت دارند.
        if not user.has_perm("ehr.add_vitalsigns"):
            return False
        today = timezone.localdate()
        return (
            patient.appointments.filter(date=today).exclude(status="cancelled").exists()
            or patient.encounters.filter(started_at__date=today).exists()
        )
    return (
        patient.appointments.filter(doctor=doctor).exclude(status="cancelled").exists()
        or patient.encounters.filter(provider=doctor).exists()
        or patient.referrals.filter(to_provider=doctor).exclude(status="cancelled").exists()
    )


def _grants(request):
    return request.session.get(SESSION_KEY, {})


def has_emergency_grant(request, patient):
    expires = _grants(request).get(str(patient.pk))
    return bool(expires) and datetime.datetime.fromisoformat(expires) > timezone.now()


def access_kind(request, patient):
    """نوع دسترسی مجاز کاربر به پرونده بالینی یا None (باید دلیل ثبت کند)."""
    user = request.user
    if not user.has_perm("ehr.view_clinical_record"):
        return None
    if user.has_perm("ehr.view_all_records"):
        return RecordAccess.Kind.ALL
    if has_care_relationship(user, patient):
        return RecordAccess.Kind.CARE
    if has_emergency_grant(request, patient):
        return RecordAccess.Kind.EMERGENCY
    return None


def grant_emergency(request, patient, reason):
    grants = dict(_grants(request))
    grants[str(patient.pk)] = (timezone.now() + datetime.timedelta(hours=EMERGENCY_GRANT_HOURS)).isoformat()
    request.session[SESSION_KEY] = grants
    return log_access(request, patient, RecordAccess.Kind.EMERGENCY, reason)


def log_access(request, patient, kind, reason=""):
    return RecordAccess.objects.create(
        patient=patient, user=request.user, kind=kind, reason=reason[:300], ip=client_ip(request)
    )


# ---------------------------------------------------------------- خط زمانی


@dataclass
class Event:
    kind: str
    kind_label: str
    when: datetime.datetime
    title: str
    status: str = ""
    by: str = ""
    lines: list = field(default_factory=list)
    url: str = ""


KINDS = {
    "encounter": "ویزیت و شرح حال",
    "vitals": "علائم حیاتی",
    "prescription": "نسخه و مشاوره دارویی",
    "document": "مدارک بیرونی",
    "appointment": "نوبت",
    "lab": "آزمایش",
    "drug": "دارو",
    "equipment": "تجهیزات پزشکی",
    "other": "سایر اقلام داروخانه",
}


def _aware(date, time=None):
    return timezone.make_aware(datetime.datetime.combine(date, time or datetime.time(0, 0)))


def _encounter_events(patient):
    from django.urls import reverse

    encounters = patient.encounters.select_related("provider").prefetch_related("diagnoses__icd", "addenda")
    for e in encounters:
        lines = []
        if e.chief_complaint:
            lines.append(f"شکایت اصلی: {e.chief_complaint}")
        lines += [f"تشخیص: {d.label}" for d in e.diagnoses.all()]
        if e.plan:
            lines.append(f"برنامه: {e.plan[:200]}")
        if e.addenda.all():
            lines.append(f"{len(e.addenda.all())} یادداشت تکمیلی")
        yield Event("encounter", KINDS["encounter"], e.started_at, f"{e.get_kind_display()} {e.provider.full_name}",
                    status=e.get_status_display(), by=e.provider.full_name, lines=lines,
                    url=reverse("ehr:encounter", args=[e.pk]))


def _prescription_events(patient):
    from django.urls import reverse

    prescriptions = patient.prescriptions.exclude(status="draft").select_related("prescriber").prefetch_related(
        "items__generic", "items__product")
    for rx in prescriptions:
        lines = [f"{item.label} × {item.quantity}" + (f" — {item.sig}" if item.sig else "") for item in rx.items.all()]
        status = rx.get_status_display() if rx.status == "cancelled" else rx.dispense_status
        yield Event("prescription", KINDS["prescription"], rx.issued_at or rx.created_at, f"نسخه {rx.number}",
                    status=status, by=rx.prescriber.full_name, lines=lines, url=reverse("ehr:prescription", args=[rx.pk]))
    for c in patient.counselings.select_related("user"):
        yield Event("prescription", "مشاوره دارویی", c.created_at, f"مشاوره دارویی: {c.topics}",
                    by=c.user.get_full_name() or c.user.username, lines=[c.notes] if c.notes else [])


def _document_events(patient):
    from django.urls import reverse

    for d in patient.documents.prefetch_related("files", "values__test"):
        lines = [str(v) for v in d.values.all()]
        if d.note:
            lines.append(d.note[:200])
        lines.append(f"{len(d.files.all())} فایل")
        yield Event("document", KINDS["document"], _aware(d.document_date, datetime.time(12)), f"{d.get_kind_display()}: {d.title}",
                    status="اشتباه ثبت شده" if d.is_void else "", by=d.source, lines=lines,
                    url=reverse("ehr:document", args=[d.pk]))
    for r in patient.lab_requests.select_related("doctor").prefetch_related("tests", "panels"):
        names = [t.name for t in r.tests.all()] + [p.name for p in r.panels.all()]
        yield Event("lab", KINDS["lab"], r.created_at, f"درخواست آزمایش {r.number}", status=r.get_status_display(),
                    by=r.doctor.full_name, lines=[", ".join(names)])


def _vitals_events(patient):
    for v in patient.vitals.all():
        parts = []
        if v.bp:
            parts.append(f"فشار {v.bp}")
        for label, value in (("نبض", v.pulse), ("دما", v.temperature), ("SpO2", v.spo2), ("وزن", v.weight),
                             ("BMI", v.bmi)):
            if value is not None:
                parts.append(f"{label} {value}")
        if v.glucose:
            parts.append(f"قند {v.glucose} ({v.get_glucose_kind_display()})")
        yield Event("vitals", KINDS["vitals"], v.recorded_at, " · ".join(parts) or "علائم حیاتی")


def _appointment_events(patient):
    for a in patient.appointments.filter(encounter__isnull=True).select_related("doctor__specialty"):
        specialty = f" ({a.doctor.specialty})" if a.doctor.specialty_id else ""
        yield Event("appointment", KINDS["appointment"], _aware(a.date, a.time), f"نوبت {a.doctor.full_name}{specialty}",
                    status=a.get_status_display(), by=a.doctor.full_name)


def _lab_events(patient):
    lab_patient = getattr(patient, "lab_patient", None)
    if lab_patient is None:
        return
    orders = lab_patient.orders.filter(status=DocStatus.POSTED).prefetch_related("items__test", "items__result")
    for order in orders:
        lines = []
        for item in order.items.all():
            result = getattr(item, "result", None)
            if result is not None and result.status == "approved":
                value = f"{result.value} {result.unit}".strip()
                flag = f" [{result.get_flag_display()}]" if result.flag else ""
                lines.append(f"{item.test.name}: {value}{flag}")
            else:
                lines.append(f"{item.test.name}: در انتظار جواب")
        by = order.referring_doctor and f"درخواست: {order.referring_doctor}"
        yield Event("lab", KINDS["lab"], order.posted_at or _aware(order.date), f"پذیرش آزمایشگاه {order.number}",
                    status=order.get_progress_display(), by=by or "", lines=lines)


def _sales_events(patient):
    invoices = patient.sales_invoices.filter(status=DocStatus.POSTED).prefetch_related("lines__product")
    for invoice in invoices:
        groups = {}
        for line in invoice.lines.all():
            kind = line.product.kind if line.product.kind in ("drug", "equipment") else "other"
            groups.setdefault(kind, []).append(f"{line.product.name} × {line.quantity}")
        prescriber = invoice.prescriber_name and f"تجویز: {invoice.prescriber_name}"
        rx = f" · نسخه {invoice.prescription_number}" if invoice.prescription_number else ""
        for kind, lines in groups.items():
            yield Event(kind, KINDS[kind], invoice.posted_at or _aware(invoice.date), f"تحویل از داروخانه {invoice.number}{rx}",
                        by=prescriber or "", lines=lines)


def _return_events(patient):
    from apps.sales.models import SalesReturn

    returns = SalesReturn.objects.filter(invoice__patient=patient, status=DocStatus.POSTED).prefetch_related(
        "lines__invoice_line__product"
    ).select_related("invoice")
    for ret in returns:
        groups = {}
        for line in ret.lines.all():
            product = line.invoice_line.product
            kind = product.kind if product.kind in ("drug", "equipment") else "other"
            groups.setdefault(kind, []).append(f"{product.name} × {line.quantity}")
        for kind, lines in groups.items():
            yield Event(kind, KINDS[kind], ret.posted_at or _aware(ret.date), f"مرجوعی {ret.number} از فاکتور {ret.invoice.number}",
                        status="برگشت داده شد", lines=lines)


def timeline(patient, kind=None):
    events = [*_encounter_events(patient), *_prescription_events(patient), *_document_events(patient), *_vitals_events(patient), *_appointment_events(patient), *_lab_events(patient), *_sales_events(patient), *_return_events(patient)]
    if kind:
        events = [e for e in events if e.kind == kind]
    return sorted(events, key=lambda e: e.when, reverse=True)


def search(query):
    from apps.core.validators import normalize_digits

    query = normalize_digits(query)
    if not query:
        return Patient.objects.none()
    return Patient.objects.filter(
        Q(national_code=query) | Q(foreign_id__iexact=query) | Q(file_number__iexact=query) | Q(mobile=query)
        | Q(last_name__icontains=query)
    )[:30]

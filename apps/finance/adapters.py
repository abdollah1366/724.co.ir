"""adapterهای فقط‌خواندنی به ماژول‌های دیگر برای صفحه صندوق و دسترسی مالکیتی.

مالی به مدل‌های پرونده سلامت و درمانگاه کلید خارجی ندارد؛ هر خواندنی فقط از این فایل و از طریق رجیستری اپ‌ها انجام می‌شود
تا با جدا شدن مالی به سرویس مستقل، فقط همین فایل با فراخوانی API جایگزین شود. فقط شناسه، نام و شماره پرونده خوانده
می‌شود؛ هیچ داده بالینی.
"""

from django.apps import apps

from apps.core.validators import normalize_digits


def _model(label):
    try:
        return apps.get_model(label)
    except LookupError:
        return None


def find_patient(query):
    """جست‌وجوی بیمار با کد ملی، کد اتباع یا شماره پرونده. خروجی: dict یا None."""
    Patient = _model("ehr.Patient")
    query = normalize_digits((query or "").strip())
    if Patient is None or not query:
        return None
    patient = (Patient.objects.filter(national_code=query).first() or Patient.objects.filter(foreign_id=query).first()
               or Patient.objects.filter(file_number=query).first())
    return patient_info(patient) if patient else None


def patient_info(patient):
    ident = patient.national_code or patient.foreign_id or ""
    masked = f"{ident[:3]}****{ident[-3:]}" if len(ident) >= 7 else ident
    return {"id": patient.pk, "name": patient.full_name, "file_number": patient.file_number, "masked_id": masked,
            "user_id": patient.user_id}


def get_patient(patient_id):
    Patient = _model("ehr.Patient")
    if Patient is None or not str(patient_id or "").isdigit():
        return None
    patient = Patient.objects.filter(pk=int(patient_id)).first()
    return patient_info(patient) if patient else None


def doctors():
    Doctor = _model("clinic.Doctor")
    if Doctor is None:
        return []
    return [(d.pk, str(d), d.visit_fee) for d in Doctor.objects.filter(is_active=True).order_by("last_name", "first_name")]


def doctor_name(doctor_id):
    Doctor = _model("clinic.Doctor")
    doctor = Doctor.objects.filter(pk=doctor_id).first() if Doctor and doctor_id else None
    return str(doctor) if doctor else ""


def priced_services():
    """خدمات درمانگاه با تعرفه ثبت‌شده: (شناسه، عنوان، تعرفه، کد ملی خدمت)."""
    Service = _model("clinic.Service")
    if Service is None:
        return []
    rows = Service.objects.filter(is_active=True, price__isnull=False).select_related("service_code").order_by("title")
    return [(s.pk, s.title, s.price, s.service_code.code if s.service_code_id else "") for s in rows]


def provider_id_for(user):
    """شناسه پزشک/ارائه‌دهنده متصل به کاربر (برای دیدن سهم و تسویه خودش)."""
    doctor = getattr(user, "doctor", None) if user.is_authenticated else None
    return doctor.pk if doctor is not None else None

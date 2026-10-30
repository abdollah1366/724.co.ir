import datetime

from django import forms
from django.utils import timezone

from apps.core.templatetags.persian import fa_digits, jalali
from apps.core.validators import normalize_digits, validate_mobile, validate_national_code

from .models import Appointment


class BookingForm(forms.Form):
    date = forms.ChoiceField(label="تاریخ")
    time = forms.ChoiceField(label="ساعت")
    patient_full_name = forms.CharField(label="نام و نام خانوادگی", max_length=150)
    national_code = forms.CharField(label="کد ملی (کد شناسایی)", max_length=10)
    mobile = forms.CharField(label="شماره موبایل", max_length=11)
    consent = forms.BooleanField(
        label="با قوانین حریم خصوصی موافقم و اجازه می‌دهم اطلاعاتم فقط برای ثبت و یادآوری نوبت استفاده شود.",
    )

    def __init__(self, *args, doctor, selected_date=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.doctor = doctor
        dates = doctor.available_dates()
        self.fields["date"].choices = [(d.isoformat(), jalali(d)) for d in dates]

        raw_date = self.data.get("date") if self.is_bound else selected_date
        self.selected_date = self._parse_date(raw_date) if raw_date else None
        if self.selected_date not in dates:
            self.selected_date = None
        if self.selected_date:
            self.initial.setdefault("date", self.selected_date.isoformat())
            slots = doctor.available_slots(self.selected_date)
        else:
            slots = []
        self.fields["time"].choices = [(t.strftime("%H:%M"), fa_digits(t.strftime("%H:%M"))) for t in slots]

    @staticmethod
    def _parse_date(value):
        if isinstance(value, datetime.date):
            return value
        try:
            return datetime.date.fromisoformat(value)
        except (TypeError, ValueError):
            return None

    def clean_national_code(self):
        value = normalize_digits(self.cleaned_data["national_code"])
        validate_national_code(value)
        return value

    def clean_mobile(self):
        value = normalize_digits(self.cleaned_data["mobile"])
        validate_mobile(value)
        return value

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("date"):
            cleaned["date"] = self._parse_date(cleaned["date"])
        if cleaned.get("time"):
            cleaned["time"] = datetime.time.fromisoformat(cleaned["time"])
        return cleaned

    def save(self, user=None):
        data = self.cleaned_data
        return Appointment.objects.create(
            doctor=self.doctor,
            user=user if user and user.is_authenticated else None,
            date=data["date"],
            time=data["time"],
            patient_full_name=data["patient_full_name"],
            national_code=data["national_code"],
            mobile=data["mobile"],
            consent_at=timezone.now(),
        )


class TrackingForm(forms.Form):
    """پیگیری نوبت با یکی از این‌ها: کد پیگیری (۸ رقم)، کد ملی (۱۰ رقم) یا شماره موبایل (۱۱ رقم).

    با موبایل یا کد ملی همه نوبت‌های آن شماره دیده می‌شود (مثلاً پدر یا مادری که برای چند فرزند نوبت گرفته)؛
    لغو نوبت فقط با کد پیگیری یا در مرورگری که نوبت را ثبت کرده ممکن است.
    """

    identifier = forms.CharField(label="کد پیگیری، کد ملی یا شماره موبایل", max_length=11)

    def __init__(self, data=None, *args, **kwargs):
        if data is not None and "identifier" not in data:
            # فرم قدیمی: کد پیگیری یا موبایل
            data = data.copy()
            data["identifier"] = data.get("tracking_code") or data.get("mobile", "")
        super().__init__(data, *args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        value = normalize_digits(cleaned.get("identifier", "")).strip()
        recent = timezone.localdate() - datetime.timedelta(days=30)
        appointments = Appointment.objects.none()
        if len(value) == 8 and value.isdigit():
            cleaned["by_code"] = True
            appointments = Appointment.objects.filter(tracking_code=value)
        elif len(value) in (10, 11) and value.isdigit():
            cleaned["by_code"] = False
            field = "national_code" if len(value) == 10 else "mobile"
            appointments = Appointment.objects.filter(**{field: value}, date__gte=recent).exclude(
                status=Appointment.Status.CANCELLED)
        appointments = list(appointments.select_related("doctor").order_by("date", "time")[:20])
        if not appointments:
            # پیام عمومی تا وجود یا عدم وجود نوبت افشا نشود.
            raise forms.ValidationError("نوبتی با این مشخصات یافت نشد.")
        cleaned["appointments"] = appointments
        return cleaned


class ReceptionBookingForm(forms.Form):
    """نوبت حضوری یا تلفنی که منشی / پذیرش برای بیمار ثبت می‌کند؛ پرونده بیمار با کد ملی یکی می‌شود."""

    national_code = forms.CharField(label="کد ملی (کد شناسایی)", max_length=10)
    first_name = forms.CharField(label="نام", max_length=80)
    last_name = forms.CharField(label="نام خانوادگی", max_length=80)
    mobile = forms.CharField(label="شماره موبایل", max_length=11)
    time = forms.ChoiceField(label="ساعت آزاد", required=False)
    custom_time = forms.TimeField(
        label="یا ساعت دلخواه (خارج از برنامه، مثل ۱۸:۳۰)", required=False,
        help_text="فقط اگر پزشک بیمار اضافه می‌پذیرد؛ ساعتی که نوبت دارد پذیرفته نمی‌شود.",
    )
    source = forms.ChoiceField(
        label="نحوه مراجعه", choices=[c for c in Appointment.Source.choices if c[0] != Appointment.Source.ONLINE],
        initial=Appointment.Source.IN_PERSON,
    )
    identity_checked = forms.BooleanField(label="کارت ملی / مدرک شناسایی بیمار را دیدم", required=False)
    consent = forms.BooleanField(label="بیمار با ثبت اطلاعاتش برای نوبت و پیامک یادآوری موافق است")
    check_in_now = forms.BooleanField(label="بیمار الان حاضر است؛ شماره صف هم صادر شود (فقط نوبت امروز)", required=False)
    special = forms.BooleanField(label="اولویت‌دار (سالمند، باردار، معلول، کودک)", required=False)

    def __init__(self, data=None, *args, doctor, date, **kwargs):
        if data is not None:
            data = data.copy()
            for name in ("national_code", "mobile", "custom_time"):
                if name in data:
                    data[name] = normalize_digits(data[name]).strip()
        super().__init__(data, *args, **kwargs)
        self.doctor, self.date = doctor, date
        self.slots = doctor.available_slots(date, staff=True)
        self.fields["time"].choices = [("", "— انتخاب ساعت —")] + [
            (t.strftime("%H:%M"), fa_digits(t.strftime("%H:%M"))) for t in self.slots]
        if date != timezone.localdate():
            del self.fields["check_in_now"]

    def clean_national_code(self):
        value = normalize_digits(self.cleaned_data["national_code"])
        validate_national_code(value)
        return value

    def clean_mobile(self):
        value = normalize_digits(self.cleaned_data["mobile"])
        validate_mobile(value)
        return value

    def clean(self):
        cleaned = super().clean()
        chosen, custom = cleaned.get("time"), cleaned.get("custom_time")
        if chosen:
            cleaned["time"] = datetime.time.fromisoformat(chosen)
        elif custom:
            custom = custom.replace(second=0, microsecond=0)
            today = timezone.localdate()
            if self.date < today or (self.date == today and custom <= timezone.localtime().time()):
                self.add_error("custom_time", "این ساعت گذشته است.")
            elif self.doctor.appointments.active().filter(date=self.date, time=custom).exists():
                self.add_error("custom_time", "این ساعت قبلاً نوبت دارد.")
            cleaned["time"] = custom
        else:
            raise forms.ValidationError("یک ساعت آزاد انتخاب کنید یا ساعت دلخواه را بنویسید.")
        return cleaned

    def save(self, user):
        """پرونده بیمار را پیدا یا می‌سازد و نوبت تأییدشده را ثبت می‌کند (داخل transaction.atomic صدا زده شود)."""
        from apps.ehr import services

        data = self.cleaned_data
        patient = services.reception_patient(
            data["national_code"], verified=data["identity_checked"],
            first_name=data["first_name"], last_name=data["last_name"], mobile=data["mobile"],
        )
        return Appointment.objects.create(
            doctor=self.doctor, date=self.date, time=data["time"], patient=patient,
            patient_full_name=f"{data['first_name']} {data['last_name']}".strip(),
            national_code=data["national_code"], mobile=data["mobile"],
            status=Appointment.Status.CONFIRMED, source=data["source"], created_by=user, consent_at=timezone.now(),
        )

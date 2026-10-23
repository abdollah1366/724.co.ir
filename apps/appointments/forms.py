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
    national_code = forms.CharField(label="کد ملی", max_length=10)
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
    tracking_code = forms.CharField(label="کد پیگیری", max_length=8)
    mobile = forms.CharField(label="شماره موبایل", max_length=11)

    def clean(self):
        cleaned = super().clean()
        code = normalize_digits(cleaned.get("tracking_code", ""))
        mobile = normalize_digits(cleaned.get("mobile", ""))
        appointment = Appointment.objects.select_related("doctor").filter(tracking_code=code, mobile=mobile).first()
        if not appointment:
            # پیام عمومی تا وجود یا عدم وجود کد پیگیری افشا نشود.
            raise forms.ValidationError("نوبتی با این مشخصات یافت نشد.")
        cleaned["appointment"] = appointment
        return cleaned

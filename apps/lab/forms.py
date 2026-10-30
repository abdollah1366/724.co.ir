import datetime

import jdatetime
from django import forms
from django.conf import settings
from django.utils import timezone

from apps.core.templatetags.persian import fa_digits, jalali
from apps.core.validators import normalize_digits, validate_mobile, validate_national_code

from .models import (
    ConsultRequest, Feedback, Interaction, InsurancePlan, LabOrder, LabPanel, LabPatient, LabPayment, LabTest, SamplingAppointment, SamplingSchedule,
)
from .services import booking


class DigitsMixin:
    """ارقام فارسی فیلدهای مشخص‌شده را به لاتین تبدیل می‌کند."""

    digit_fields = ()

    def _normalize(self, data):
        if data is None:
            return None
        data = data.copy()
        for name in self.digit_fields:
            if name in data:
                data[name] = normalize_digits(data[name])
        return data


def _clean_national_code(value):
    value = normalize_digits(value)
    validate_national_code(value)
    return value


def _clean_mobile(value):
    value = normalize_digits(value)
    validate_mobile(value)
    return value


class SamplingBookingForm(DigitsMixin, forms.Form):
    digit_fields = ("national_code", "mobile")

    time = forms.ChoiceField(label="ساعت")
    full_name = forms.CharField(label="نام و نام خانوادگی", max_length=150)
    national_code = forms.CharField(label="کد ملی", max_length=10)
    mobile = forms.CharField(label="شماره موبایل", max_length=11)
    address = forms.CharField(label="آدرس کامل برای نمونه‌گیری در منزل", max_length=300, required=False,
                              widget=forms.Textarea(attrs={"rows": 2}))
    tests = forms.ModelMultipleChoiceField(
        label="آزمایش‌های نسخه (اختیاری؛ برای آماده‌سازی پذیرش)", queryset=LabTest.objects.none(), required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    note = forms.CharField(label="توضیح برای پذیرش (اطلاعات بیماری وارد نکنید)", max_length=300, required=False)
    consent = forms.BooleanField(
        label="با قوانین حریم خصوصی موافقم و اجازه می‌دهم اطلاعاتم فقط برای نوبت، پذیرش و اطلاع‌رسانی جواب استفاده شود.",
    )

    def __init__(self, data=None, *, kind, date, **kwargs):
        super().__init__(self._normalize(data), **kwargs)
        self.kind, self.date = kind, date
        self.fields["time"].choices = [
            (t.strftime("%H:%M"), f"{fa_digits(t.strftime('%H:%M'))} ({fa_digits(left)} جای خالی)")
            for t, left in booking.available_slots(kind, date)
        ]
        tests = LabTest.objects.public()
        if kind == SamplingSchedule.Kind.HOME:
            tests = tests.filter(home_sampling=True)
        self.fields["tests"].queryset = tests
        if kind != SamplingSchedule.Kind.HOME:
            del self.fields["address"]

    def clean_national_code(self):
        return _clean_national_code(self.cleaned_data["national_code"])

    def clean_mobile(self):
        return _clean_mobile(self.cleaned_data["mobile"])

    def clean_address(self):
        value = self.cleaned_data.get("address", "").strip()
        if not value:
            raise forms.ValidationError("آدرس برای نمونه‌گیری در منزل الزامی است.")
        return value

    def clean_time(self):
        return datetime.time.fromisoformat(self.cleaned_data["time"])


class BookingTrackForm(DigitsMixin, forms.Form):
    """پیگیری با یکی از این‌ها: کد پیگیری، کد ملی یا شماره موبایل. لغو فقط با کد پیگیری."""

    digit_fields = ("identifier",)
    identifier = forms.CharField(label="کد پیگیری، کد ملی یا شماره موبایل", max_length=11)

    def __init__(self, data=None, **kwargs):
        if data is not None and "identifier" not in data:
            data = data.copy()
            data["identifier"] = data.get("tracking_code") or data.get("mobile", "")
        super().__init__(self._normalize(data), **kwargs)

    def clean(self):
        cleaned = super().clean()
        value = cleaned.get("identifier", "").strip()
        recent = timezone.localdate() - datetime.timedelta(days=30)
        if len(value) in (10, 11) and value.isdigit():
            cleaned["by_code"] = False
            field = "national_code" if len(value) == 10 else "mobile"
            found = SamplingAppointment.objects.active().filter(**{field: value}, date__gte=recent)
        else:
            cleaned["by_code"] = True
            found = SamplingAppointment.objects.filter(tracking_code=value.upper()) if value else SamplingAppointment.objects.none()
        found = list(found.order_by("date", "time")[:20])
        if not found:
            raise forms.ValidationError("نوبتی با این مشخصات یافت نشد.")
        cleaned["appointments"] = found
        return cleaned


def date_choices(dates):
    return [(d.isoformat(), jalali(d)) for d in dates]


# --- پذیرش (کارکنان) ---
class PatientForm(DigitsMixin, forms.ModelForm):
    digit_fields = ("national_code", "mobile", "phone", "birth_date", "basic_insurance_no", "supplementary_insurance_no")
    birth_date = forms.CharField(label="تاریخ تولد (شمسی، مثل ۱۳۶۵/۰۴/۲۰)")
    reminder_consent = forms.BooleanField(label="بیمار با پیامک یادآوری آزمایش دوره‌ای موافق است", required=False)

    class Meta:
        model = LabPatient
        fields = [
            "national_code", "foreign_id", "first_name", "last_name", "father_name", "sex", "birth_date", "mobile",
            "phone", "address", "basic_plan", "basic_insurance_no", "supplementary_plan", "supplementary_insurance_no",
        ]

    def __init__(self, data=None, *args, **kwargs):
        super().__init__(self._normalize(data), *args, **kwargs)
        self.fields["national_code"].label = "کد ملی (کد شناسایی)"
        for name in ("basic_plan", "supplementary_plan"):
            self.fields[name].queryset = self.fields[name].queryset.filter(is_active=True)
        if self.instance.pk:
            self.initial["birth_date"] = jdatetime.date.fromgregorian(date=self.instance.birth_date).strftime("%Y/%m/%d")
            self.initial["reminder_consent"] = bool(self.instance.reminder_consent_at)
        for name in ("national_code", "foreign_id"):
            self.fields[name].required = False

    def clean_national_code(self):
        value = normalize_digits(self.cleaned_data.get("national_code") or "")
        if not value:
            return None
        return _clean_national_code(value)

    def clean_foreign_id(self):
        return (self.cleaned_data.get("foreign_id") or "").strip() or None

    def clean_birth_date(self):
        value = self.cleaned_data["birth_date"].replace("-", "/")
        try:
            year, month, day = (int(p) for p in value.split("/"))
            date = jdatetime.date(year, month, day).togregorian() if year < 1700 else datetime.date(year, month, day)
        except (ValueError, TypeError):
            raise forms.ValidationError("تاریخ تولد را به شکل ۱۳۶۵/۰۴/۲۰ وارد کنید.")
        if date > timezone.localdate():
            raise forms.ValidationError("تاریخ تولد در آینده است.")
        return date

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("national_code") and not cleaned.get("foreign_id"):
            raise forms.ValidationError("کد ملی یا شماره گذرنامه / کد اتباع را وارد کنید.")
        return cleaned

    def save(self, commit=True):
        patient = super().save(commit=False)
        consent = self.cleaned_data.get("reminder_consent")
        if consent and not patient.reminder_consent_at:
            patient.reminder_consent_at = timezone.now()
        elif not consent:
            patient.reminder_consent_at = None
        if commit:
            patient.save()
        return patient


class OrderForm(DigitsMixin, forms.ModelForm):
    digit_fields = ("basic_insurance_no", "supplementary_insurance_no", "referring_council_no", "discount", "home_fee")
    tests = forms.ModelMultipleChoiceField(
        label="آزمایش‌ها", queryset=LabTest.objects.filter(is_active=True), required=False, widget=forms.CheckboxSelectMultiple
    )
    panels = forms.ModelMultipleChoiceField(
        label="بسته‌ها", queryset=LabPanel.objects.filter(is_active=True), required=False, widget=forms.CheckboxSelectMultiple
    )

    class Meta:
        model = LabOrder
        fields = [
            "priority", "sampling_place", "home_fee", "referring_doctor", "referring_council_no", "prescription_code",
            "basic_plan", "basic_insurance_no", "basic_approval_code", "supplementary_plan", "supplementary_insurance_no",
            "supplementary_approval_code", "discount", "discount_reason", "delivery", "clinical_note", "note",
        ]

    def __init__(self, data=None, *args, user=None, **kwargs):
        super().__init__(self._normalize(data), *args, **kwargs)
        self.fields["basic_plan"].queryset = InsurancePlan.objects.filter(kind=InsurancePlan.Kind.BASIC, is_active=True)
        self.fields["supplementary_plan"].queryset = InsurancePlan.objects.filter(
            kind=InsurancePlan.Kind.SUPPLEMENTARY, is_active=True
        )
        if user is not None and not user.has_perm("lab.give_lab_discount"):
            del self.fields["discount"]
            del self.fields["discount_reason"]
        if self.instance.pk:
            items = self.instance.items.all()
            self.initial["tests"] = [i.test_id for i in items if i.panel_id is None]
            self.initial["panels"] = list({i.panel_id for i in items if i.panel_id})

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("tests") and not cleaned.get("panels"):
            raise forms.ValidationError("حداقل یک آزمایش یا بسته انتخاب کنید.")
        if cleaned.get("sampling_place") == SamplingSchedule.Kind.HOME and not cleaned.get("home_fee"):
            cleaned["home_fee"] = settings.LAB_RULES["home_sampling_fee"]
        if cleaned.get("sampling_place") != SamplingSchedule.Kind.HOME:
            cleaned["home_fee"] = 0
        return cleaned


class PaymentForm(DigitsMixin, forms.ModelForm):
    digit_fields = ("amount", "reference_no")

    class Meta:
        model = LabPayment
        fields = ["direction", "method", "amount", "reference_no"]

    def __init__(self, data=None, *args, **kwargs):
        super().__init__(self._normalize(data), *args, **kwargs)


class ReasonForm(forms.Form):
    reason = forms.CharField(label="علت", max_length=300)


class ConsultForm(DigitsMixin, forms.ModelForm):
    digit_fields = ("mobile",)
    consent = forms.BooleanField(
        label="اجازه می‌دهم کارشناس آزمایشگاه برای پاسخ به همین درخواست با من تماس بگیرد.",
    )

    class Meta:
        model = ConsultRequest
        fields = ["topic", "full_name", "mobile", "preferred_time", "message"]
        widgets = {"message": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, data=None, *args, staff=False, **kwargs):
        super().__init__(self._normalize(data), *args, **kwargs)
        if staff:  # ثبت تماس تلفنی یا مراجعه حضوری توسط کارشناس
            self.fields["consent"].label = "مشتری با تماس برای پیگیری موافق است."
            self.fields["channel"] = forms.ChoiceField(
                label="کانال", choices=[c for c in ConsultRequest.Channel.choices if c[0] != "web"]
            )

    def save(self, commit=True):
        consult = super().save(commit=False)
        consult.consent_at = timezone.now()
        if "channel" in self.cleaned_data:
            consult.channel = self.cleaned_data["channel"]
        if commit:
            consult.save()
        return consult


class InteractionForm(forms.Form):
    kind = forms.ChoiceField(label="نوع", choices=Interaction.Kind.choices)
    text = forms.CharField(label="شرح گفت‌وگو یا اقدام", max_length=2000, widget=forms.Textarea(attrs={"rows": 3}))
    status = forms.ChoiceField(label="وضعیت جدید", choices=[("", "بدون تغییر")] + ConsultRequest.Status.choices, required=False)
    next_follow_up = forms.DateTimeField(
        label="پیگیری بعدی", required=False, widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
        input_formats=["%Y-%m-%dT%H:%M"],
    )


RATING = [(i, str(i)) for i in range(5, 0, -1)]


class FeedbackForm(forms.ModelForm):
    overall = forms.TypedChoiceField(label="رضایت کلی از آزمایشگاه", choices=RATING, coerce=int, widget=forms.RadioSelect)
    waiting = forms.TypedChoiceField(label="زمان انتظار", choices=RATING, coerce=int, widget=forms.RadioSelect, required=False, empty_value=None)
    staff = forms.TypedChoiceField(label="برخورد کارکنان", choices=RATING, coerce=int, widget=forms.RadioSelect, required=False, empty_value=None)
    sampling = forms.TypedChoiceField(label="نمونه‌گیری", choices=RATING, coerce=int, widget=forms.RadioSelect, required=False, empty_value=None)

    class Meta:
        model = Feedback
        fields = ["overall", "waiting", "staff", "sampling", "comment", "wants_contact"]
        labels = {"comment": "پیشنهاد یا انتقاد (اختیاری)", "wants_contact": "مایلم کارشناس با من تماس بگیرد"}
        widgets = {"comment": forms.Textarea(attrs={"rows": 3})}

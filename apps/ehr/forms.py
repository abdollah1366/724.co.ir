import datetime

import jdatetime
from django import forms
from django.utils import timezone

from apps.core.validators import normalize_digits
from apps.lab.forms import DigitsMixin

from .models import Reminder, Assessment, TherapySession, TreatmentPlan, Allergy, DrugCounseling, ExternalDocument, ExternalLabValue, LabRequest, EncounterService, GenericDrug, Prescription, ServiceCode, Diagnosis, Encounter, HistoryEntry, ICD10Code, Patient, Problem, Referral, VitalSigns
from .models.rehab import AUDIOGRAM_FREQUENCIES


def parse_jalali(value, allow_future=True):
    value = normalize_digits(value or "").replace("-", "/").strip()
    if not value:
        return None
    try:
        year, month, day = (int(p) for p in value.split("/"))
        date = jdatetime.date(year, month, day).togregorian() if year < 1700 else datetime.date(year, month, day)
    except (ValueError, TypeError):
        raise forms.ValidationError("تاریخ را به شکل ۱۴۰۵/۰۸/۰۱ وارد کنید.")
    if not allow_future and date > timezone.localdate():
        raise forms.ValidationError("تاریخ در آینده است.")
    return date


class JalaliDateField(forms.CharField):
    def __init__(self, *args, allow_future=True, **kwargs):
        self.allow_future = allow_future
        kwargs.setdefault("required", False)
        super().__init__(*args, **kwargs)

    def prepare_value(self, value):
        if isinstance(value, datetime.date):
            return jdatetime.date.fromgregorian(date=value).strftime("%Y/%m/%d")
        return value

    def clean(self, value):
        return parse_jalali(super().clean(value), self.allow_future)


class PatientForm(DigitsMixin, forms.ModelForm):
    """فرم پذیرش؛ ذخیره آن یعنی هویت با مدرک شناسایی تطبیق داده شده است."""

    digit_fields = ("national_code", "mobile", "phone", "birth_date")
    birth_date = forms.CharField(label="تاریخ تولد (شمسی، مثل ۱۳۶۵/۰۴/۲۰)", required=False)
    reminder_consent = forms.BooleanField(
        label="بیمار با دریافت پیامک یادآوری ویزیت و دارو موافق است", required=False,
        help_text="متن پیامک هیچ اطلاعات پزشکی ندارد. رضایت را از خود بیمار بپرسید.",
    )

    class Meta:
        model = Patient
        fields = [
            "national_code", "foreign_id", "first_name", "last_name", "father_name", "sex", "birth_date", "mobile",
            "phone", "address", "blood_group", "emergency_contact",
        ]

    def __init__(self, data=None, *args, **kwargs):
        super().__init__(self._normalize(data), *args, **kwargs)
        for name in ("first_name", "last_name", "sex", "mobile"):
            self.fields[name].required = True
        self.initial["reminder_consent"] = bool(self.instance.reminder_consent_at)
        if self.instance.pk and self.instance.birth_date:
            self.initial["birth_date"] = jdatetime.date.fromgregorian(date=self.instance.birth_date).strftime("%Y/%m/%d")

    def clean_national_code(self):
        return normalize_digits(self.cleaned_data.get("national_code") or "") or None

    def save(self, commit=True):
        patient = super().save(commit=False)
        if self.cleaned_data.get("reminder_consent") and not patient.reminder_consent_at:
            patient.reminder_consent_at = timezone.now()
        elif not self.cleaned_data.get("reminder_consent"):
            patient.reminder_consent_at = None
        if commit:
            patient.save()
        return patient

    def clean_foreign_id(self):
        return (self.cleaned_data.get("foreign_id") or "").strip() or None

    def clean_birth_date(self):
        value = (self.cleaned_data.get("birth_date") or "").replace("-", "/").strip()
        if not value:
            return None
        try:
            year, month, day = (int(p) for p in value.split("/"))
            date = jdatetime.date(year, month, day).togregorian() if year < 1700 else datetime.date(year, month, day)
        except (ValueError, TypeError):
            raise forms.ValidationError("تاریخ تولد را به شکل ۱۳۶۵/۰۴/۲۰ وارد کنید.")
        if date > timezone.localdate():
            raise forms.ValidationError("تاریخ تولد در آینده است.")
        return date

    def clean(self):
        data = super().clean()
        if not data.get("national_code") and not data.get("foreign_id"):
            raise forms.ValidationError("کد ملی یا برای اتباع، شماره گذرنامه / کد اتباع لازم است.")
        return data


class EmergencyAccessForm(forms.Form):
    reason = forms.CharField(
        label="دلیل مشاهده پرونده", min_length=10, max_length=300, widget=forms.Textarea(attrs={"rows": 3}),
        help_text="مثلاً «بیمار بدون نوبت مراجعه کرده و ویزیت می‌شود». این دلیل با نام شما ثبت و توسط مسئول فنی بررسی می‌شود.",
    )


class IcdMixin:
    """کد ICD-10 به صورت متن (مثل E11.9) وارد و به کاتالوگ وصل می‌شود."""

    def _icd_field(self):
        self.fields["icd_code"] = forms.CharField(
            label="کد ICD-10", max_length=10, required=False,
            widget=forms.TextInput(attrs={"list": "icd-options", "data-icd-search": "1", "dir": "ltr", "autocomplete": "off"}),
        )
        if getattr(self.instance, "icd_id", None):
            self.initial["icd_code"] = self.instance.icd.code

    def clean_icd_code(self):
        code = (self.cleaned_data.get("icd_code") or "").strip().upper().split(" ")[0]
        if not code:
            return None
        icd = ICD10Code.objects.filter(code=code, is_active=True).first()
        if icd is None:
            raise forms.ValidationError("این کد در فهرست ICD-10 نیست.")
        self.instance.icd = icd  # پیش از اعتبارسنجی مدل
        return icd

    def _apply_icd(self, instance):
        instance.icd = self.cleaned_data.get("icd_code")
        return instance


class EncounterForm(forms.ModelForm):
    next_visit = JalaliDateField(label="تاریخ ویزیت بعدی (شمسی)")

    class Meta:
        model = Encounter
        fields = ["kind", "chief_complaint", "subjective", "objective", "assessment", "plan", "next_visit"]
        widgets = {name: forms.Textarea(attrs={"rows": 3}) for name in ("subjective", "objective", "assessment", "plan")}


class VitalsForm(DigitsMixin, forms.ModelForm):
    digit_fields = ("systolic", "diastolic", "pulse", "respiratory_rate", "temperature", "spo2", "weight", "height",
                    "waist", "glucose")

    class Meta:
        model = VitalSigns
        fields = ["systolic", "diastolic", "pulse", "respiratory_rate", "temperature", "spo2", "weight", "height", "waist",
                  "glucose", "glucose_kind"]

    def __init__(self, data=None, *args, **kwargs):
        super().__init__(self._normalize(data), *args, **kwargs)


class DiagnosisForm(IcdMixin, forms.ModelForm):
    class Meta:
        model = Diagnosis
        fields = ["text", "is_primary", "certainty"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._icd_field()
        self.order_fields(["icd_code", "text", "is_primary", "certainty"])

    def clean(self):
        data = super().clean()
        if not data.get("icd_code") and not data.get("text"):
            raise forms.ValidationError("کد ICD-10 یا شرح تشخیص را وارد کنید.")
        return data


class ProblemForm(IcdMixin, forms.ModelForm):
    onset = JalaliDateField(label="تاریخ شروع (شمسی)", allow_future=False)

    class Meta:
        model = Problem
        fields = ["title", "chronic_program", "onset", "status", "note"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._icd_field()
        self.order_fields(["icd_code", "title", "chronic_program", "onset", "status", "note"])

    def clean(self):
        data = super().clean()
        if not data.get("icd_code") and not data.get("title"):
            raise forms.ValidationError("عنوان یا کد ICD-10 را وارد کنید.")
        return data


class AllergyForm(forms.ModelForm):
    class Meta:
        model = Allergy
        fields = ["kind", "substance", "reaction", "severity"]


class HistoryForm(forms.ModelForm):
    date = JalaliDateField(label="تاریخ (شمسی)", allow_future=False)

    class Meta:
        model = HistoryEntry
        fields = ["kind", "text", "date"]


class ReferralForm(forms.ModelForm):
    class Meta:
        model = Referral
        fields = ["to_provider", "to_service", "reason"]

    def clean(self):
        data = super().clean()
        if not data.get("to_provider") and not data.get("to_service"):
            raise forms.ValidationError("پزشک مقصد یا خدمت مقصد را مشخص کنید.")
        return data


class AddendumForm(forms.Form):
    text = forms.CharField(label="یادداشت تکمیلی / اصلاحیه", min_length=5, widget=forms.Textarea(attrs={"rows": 3}))


class EncounterServiceForm(forms.ModelForm):
    code = forms.CharField(
        label="کد ملی خدمت", max_length=12,
        widget=forms.TextInput(attrs={"list": "service-options", "data-service-search": "1", "dir": "ltr", "autocomplete": "off"}),
    )

    class Meta:
        model = EncounterService
        fields = ["quantity", "sector", "note"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.order_fields(["code", "quantity", "sector", "note"])

    def clean_code(self):
        code = normalize_digits(self.cleaned_data["code"]).split(" ")[0]
        service = ServiceCode.objects.filter(code=code, is_active=True).first()
        if service is None:
            raise forms.ValidationError("این کد در کدینگ خدمات سلامت نیست.")
        self.instance.service_code = service
        return service


class PrescriptionItemForm(DigitsMixin, forms.Form):
    """دارو با جست‌وجو انتخاب می‌شود: G12 یعنی ژنریک شماره ۱۲ و P5 یعنی کالای شماره ۵ داروخانه."""

    digit_fields = ("quantity", "duration_days")
    drug = forms.CharField(
        label="دارو / تجهیزات", max_length=300,
        widget=forms.TextInput(attrs={"list": "drug-options", "data-drug-search": "1", "autocomplete": "off"}),
        help_text="نام ژنریک یا تجاری را تایپ و از فهرست انتخاب کنید.",
    )
    quantity = forms.IntegerField(label="تعداد", min_value=1)
    dose = forms.CharField(label="مقدار هر نوبت", max_length=60, required=False)
    frequency = forms.CharField(label="دفعات مصرف", max_length=80, required=False)
    duration_days = forms.IntegerField(label="مدت مصرف (روز)", min_value=1, max_value=365, required=False)
    instructions = forms.CharField(label="دستور مصرف", max_length=300, required=False)
    override = forms.BooleanField(label="با وجود هشدار حساسیت ثبت شود (مسئولیت با پزشک)", required=False)

    def __init__(self, data=None, *args, **kwargs):
        super().__init__(self._normalize(data), *args, **kwargs)

    def clean_drug(self):
        from apps.shop.models import Product

        value = self.cleaned_data["drug"].strip().split(" ")[0]
        kind, _, pk = value[:1].upper(), None, value[1:]
        if not pk.isdigit():
            raise forms.ValidationError("دارو را از فهرست پیشنهادی انتخاب کنید.")
        if kind == "G":
            generic = GenericDrug.objects.filter(pk=pk, is_active=True).first()
            if generic:
                return {"generic": generic}
        if kind == "P":
            product = Product.objects.filter(pk=pk, is_active=True).first()
            if product:
                return {"product": product, "generic": product.generic}
        raise forms.ValidationError("دارو پیدا نشد.")


class PrescriptionMetaForm(forms.ModelForm):
    class Meta:
        model = Prescription
        fields = ["note", "insurance_tracking_code"]


class CounselingForm(forms.ModelForm):
    class Meta:
        model = DrugCounseling
        fields = ["topics", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}


class ReasonForm(forms.Form):
    reason = forms.CharField(label="علت", min_length=5, max_length=300)


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput(attrs={"accept": "image/*,application/pdf", "capture": "environment"}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single = super().clean
        files = data if isinstance(data, (list, tuple)) else [data]
        return [single(f, initial) for f in files if f] or [single(None, initial)]


class ExternalDocumentForm(forms.ModelForm):
    document_date = JalaliDateField(label="تاریخ مدرک (شمسی)", allow_future=False, required=True)
    files = MultipleFileField(
        label="فایل‌ها (PDF یا عکس؛ با گوشی می‌توانید مستقیم عکس بگیرید)",
        help_text="چند صفحه را با هم انتخاب کنید؛ هر فایل حداکثر ۱۵ مگابایت.",
    )

    class Meta:
        model = ExternalDocument
        fields = ["kind", "title", "source", "document_date", "note"]
        widgets = {"note": forms.Textarea(attrs={"rows": 3})}

    def clean_files(self):
        from .models.documents import validate_medical_file

        files = self.cleaned_data["files"]
        for f in files:
            validate_medical_file(f)
        if len(files) > 20:
            raise forms.ValidationError("حداکثر ۲۰ فایل در هر مدرک.")
        return files


class ExternalValueForm(DigitsMixin, forms.ModelForm):
    digit_fields = ("value",)

    class Meta:
        model = ExternalLabValue
        fields = ["test", "value", "unit"]

    def __init__(self, data=None, *args, **kwargs):
        super().__init__(self._normalize(data), *args, **kwargs)
        self.fields["test"].queryset = self.fields["test"].queryset.filter(result_type="numeric", is_active=True)
        self.empty_permitted = True
        self.use_required_attribute = False


ExternalValueFormSet = forms.formset_factory(ExternalValueForm, extra=3)


class LabRequestForm(forms.ModelForm):
    class Meta:
        model = LabRequest
        fields = ["tests", "panels", "is_urgent", "clinical_note"]
        widgets = {"tests": forms.CheckboxSelectMultiple, "panels": forms.CheckboxSelectMultiple}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tests"].queryset = self.fields["tests"].queryset.filter(is_active=True).order_by("section__order", "name")
        self.fields["panels"].queryset = self.fields["panels"].queryset.filter(is_active=True)
        self.fields["tests"].required = False
        self.fields["panels"].required = False


# ---------------------------------------------------------------- توانبخشی


class TreatmentPlanForm(IcdMixin, forms.ModelForm):
    start_date = JalaliDateField(label="تاریخ شروع (شمسی)", required=True)

    class Meta:
        model = TreatmentPlan
        fields = ["discipline", "referral", "diagnosis", "goals", "planned_sessions", "frequency", "start_date",
                  "share_with_physicians"]
        widgets = {"goals": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, patient=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._icd_field()
        self.order_fields(["discipline", "referral", "icd_code", "diagnosis", "goals", "planned_sessions", "frequency",
                           "start_date", "share_with_physicians"])
        self.fields["referral"].required = False
        self.fields["referral"].queryset = (
            Referral.objects.filter(patient=patient).exclude(status=Referral.Status.CANCELLED) if patient else Referral.objects.none()
        )


class TherapySessionForm(DigitsMixin, forms.ModelForm):
    digit_fields = ("pain_score", "code")
    code = forms.CharField(
        label="کد ملی خدمت", max_length=12, required=False,
        widget=forms.TextInput(attrs={"list": "service-options", "data-service-search": "1", "dir": "ltr", "autocomplete": "off"}),
    )

    class Meta:
        model = TherapySession
        fields = ["attendance", "subjective", "intervention", "response", "home_program", "pain_score", "sector"]
        widgets = {name: forms.Textarea(attrs={"rows": 2}) for name in ("subjective", "intervention", "response", "home_program")}

    def __init__(self, data=None, *args, **kwargs):
        super().__init__(self._normalize(data), *args, **kwargs)

    def clean_pain_score(self):
        score = self.cleaned_data.get("pain_score")
        if score is not None and score > 10:
            raise forms.ValidationError("شدت درد بین ۰ تا ۱۰ است.")
        return score

    def clean_code(self):
        code = (self.cleaned_data.get("code") or "").split(" ")[0]
        if not code:
            return None
        service = ServiceCode.objects.filter(code=code, is_active=True).first()
        if service is None:
            raise forms.ValidationError("این کد در کدینگ خدمات سلامت نیست.")
        self.instance.service_code = service
        return service


class AssessmentForm(DigitsMixin, forms.Form):
    """ارزیابی؛ برای ادیوگرام آستانه‌ها (dB HL) در ۶ فرکانس برای هر گوش وارد می‌شود."""

    kind = forms.ChoiceField(label="نوع ارزیابی", choices=Assessment.Kind.choices)
    score = forms.DecimalField(label="امتیاز / مقدار", required=False, max_digits=7, decimal_places=2)
    notes = forms.CharField(label="شرح", required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, data=None, *args, kinds=None, **kwargs):
        self.digit_fields = ("score", *self.ear_fields())
        super().__init__(self._normalize(data), *args, **kwargs)
        if kinds:
            self.fields["kind"].choices = [c for c in Assessment.Kind.choices if c[0] in kinds]
        for name, label in self.ear_fields().items():
            self.fields[name] = forms.IntegerField(label=label, required=False,
                                                   widget=forms.NumberInput(attrs={"class": "audiogram-input"}))

    @staticmethod
    def ear_fields():
        return {f"{ear}_{freq}": f"{label} {freq} Hz" for ear, label in (("right", "راست"), ("left", "چپ"))
                for freq in AUDIOGRAM_FREQUENCIES}

    def audiogram_values(self):
        return {ear: {freq: self.cleaned_data.get(f"{ear}_{freq}") for freq in AUDIOGRAM_FREQUENCIES}
                for ear in ("right", "left")}


class ClosePlanForm(forms.Form):
    status = forms.ChoiceField(label="نتیجه", choices=[c for c in TreatmentPlan.Status.choices if c[0] != "active"])
    summary = forms.CharField(label="خلاصه پایان درمان", min_length=5, widget=forms.Textarea(attrs={"rows": 3}))


# ---------------------------------------------------------------- یادآور و صف


class ReminderForm(DigitsMixin, forms.Form):
    digit_fields = ("due_date", "days_before")
    kind = forms.ChoiceField(label="نوع", choices=Reminder.Kind.choices)
    due_date = JalaliDateField(label="موعد مراجعه (شمسی)", required=True)
    days_before = forms.IntegerField(label="ارسال پیامک چند روز قبل", min_value=0, max_value=30, initial=1)
    note = forms.CharField(label="یادداشت داخلی (در پیامک نمی‌آید)", max_length=200, required=False)

    def __init__(self, data=None, *args, **kwargs):
        super().__init__(self._normalize(data), *args, **kwargs)

    def clean_due_date(self):
        date = self.cleaned_data["due_date"]
        if date and date < timezone.localdate():
            raise forms.ValidationError("موعد در گذشته است.")
        return date


class WalkInForm(DigitsMixin, forms.Form):
    digit_fields = ("identifier",)
    doctor = forms.ModelChoiceField(label="پزشک / درمانگر", queryset=None)
    identifier = forms.CharField(label="کد ملی یا شماره پرونده (اختیاری)", max_length=30, required=False)
    special = forms.BooleanField(label="اولویت‌دار (سالمند، باردار، معلول، کودک، اورژانسی)", required=False)

    def __init__(self, data=None, *args, **kwargs):
        from apps.clinic.models import Doctor

        super().__init__(self._normalize(data), *args, **kwargs)
        self.fields["doctor"].queryset = Doctor.objects.filter(is_active=True)

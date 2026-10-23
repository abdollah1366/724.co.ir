import datetime

import jdatetime
from django import forms
from django.utils import timezone

from apps.core.validators import normalize_digits
from apps.lab.forms import DigitsMixin

from .models import Patient


class PatientForm(DigitsMixin, forms.ModelForm):
    """فرم پذیرش؛ ذخیره آن یعنی هویت با مدرک شناسایی تطبیق داده شده است."""

    digit_fields = ("national_code", "mobile", "phone", "birth_date")
    birth_date = forms.CharField(label="تاریخ تولد (شمسی، مثل ۱۳۶۵/۰۴/۲۰)", required=False)

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
        if self.instance.pk and self.instance.birth_date:
            self.initial["birth_date"] = jdatetime.date.fromgregorian(date=self.instance.birth_date).strftime("%Y/%m/%d")

    def clean_national_code(self):
        return normalize_digits(self.cleaned_data.get("national_code") or "") or None

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

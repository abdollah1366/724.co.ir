from django import forms
from django.conf import settings

from apps.ehr.forms import MultipleFileField
from apps.ehr.models.documents import validate_medical_file

from .models import Consultation


class AttachmentsMixin:
    def clean_files(self):
        files = [f for f in self.cleaned_data.get("files") or [] if f]
        if len(files) > settings.TELEMED_MAX_FILES:
            raise forms.ValidationError(f"حداکثر {settings.TELEMED_MAX_FILES} فایل در هر پیام.")
        for f in files:
            validate_medical_file(f)
        return files


def files_field():
    return MultipleFileField(
        label="پیوست (اختیاری)", required=False,
        help_text="عکس یا PDF مثل برگه آزمایش، نسخه یا عکس جعبه دارو؛ حداکثر ۳ فایل و هر کدام ۱۵ مگابایت.",
    )


class ConsultationForm(AttachmentsMixin, forms.ModelForm):
    description = forms.CharField(
        label="شرح مشکل یا سؤال", min_length=20, max_length=4000, widget=forms.Textarea(attrs={"rows": 6}),
        help_text="از کی شروع شده، علائم، بیماری‌های زمینه‌ای، داروهایی که مصرف می‌کنید و حساسیت دارویی.",
    )
    files = files_field()
    consent = forms.BooleanField(
        label="می‌دانم مشاوره آنلاین جایگزین معاینه حضوری نیست، اطلاعاتم درست است و با شرایط مشاوره آنلاین موافقم.",
    )

    class Meta:
        model = Consultation
        fields = ["subject", "for_whom", "age", "sex", "sms_opt_in"]
        labels = {"sms_opt_in": "وقتی پاسخ آمد پیامک بزنید (متن پیامک هیچ اطلاعات پزشکی ندارد)"}

    def clean_age(self):
        age = self.cleaned_data["age"]
        if age > 120:
            raise forms.ValidationError("سن معتبر نیست.")
        return age

    def payload(self):
        return {f: self.cleaned_data[f] for f in [*self.Meta.fields, "description"]}


class MessageForm(AttachmentsMixin, forms.Form):
    text = forms.CharField(label="پیام", max_length=4000, required=False, widget=forms.Textarea(attrs={"rows": 3}))
    files = files_field()

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("text", "").strip() and not cleaned.get("files"):
            raise forms.ValidationError("متن پیام یا پیوست را وارد کنید.")
        return cleaned


class CloseForm(forms.Form):
    outcome = forms.ChoiceField(label="نتیجه مشاوره", choices=Consultation.Outcome.choices)


class DeclineForm(forms.Form):
    reason = forms.CharField(label="علت (به بیمار نشان داده می‌شود)", min_length=5, max_length=300)


class MeetingLinkForm(forms.Form):
    meeting_link = forms.URLField(label="پیوند تماس تصویری", assume_scheme="https")

    def clean_meeting_link(self):
        link = self.cleaned_data["meeting_link"]
        if not link.startswith("https://"):
            raise forms.ValidationError("پیوند باید با https شروع شود.")
        return link

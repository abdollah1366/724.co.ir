from django import forms

from apps.core.validators import normalize_digits, validate_mobile

from .models import Order, PharmacyQuestion


class MobileCleanMixin:
    def clean_mobile(self):
        value = normalize_digits(self.cleaned_data["mobile"])
        validate_mobile(value)
        return value


class CheckoutForm(MobileCleanMixin, forms.ModelForm):
    consent = forms.BooleanField(
        label="تأیید می‌کنم اطلاعات وارد شده صحیح است و با قوانین حریم خصوصی موافقم.",
    )

    class Meta:
        model = Order
        fields = ["full_name", "mobile", "delivery_method", "address", "note"]
        widgets = {
            "delivery_method": forms.RadioSelect,
            "address": forms.Textarea(attrs={"rows": 3}),
        }

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("delivery_method") == Order.Delivery.COURIER and not cleaned.get("address", "").strip():
            self.add_error("address", "برای ارسال با پیک، آدرس را وارد کنید.")
        return cleaned


class PharmacyQuestionForm(MobileCleanMixin, forms.ModelForm):
    class Meta:
        model = PharmacyQuestion
        fields = ["name", "mobile", "question"]
        widgets = {"question": forms.Textarea(attrs={"rows": 5})}
        help_texts = {"question": "نام دارو، سن و داروهای دیگری که مصرف می‌کنید را بنویسید تا پاسخ دقیق‌تری بگیرید."}

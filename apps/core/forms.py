from django import forms

from .models import ContactMessage
from .validators import normalize_digits


class ContactForm(forms.ModelForm):
    class Meta:
        model = ContactMessage
        fields = ["name", "mobile", "subject", "message"]
        widgets = {"message": forms.Textarea(attrs={"rows": 5})}

    def clean_mobile(self):
        return normalize_digits(self.cleaned_data["mobile"])

from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.contrib.auth.forms import AuthenticationForm
from django.db import transaction

from apps.core.validators import normalize_digits, validate_mobile, validate_national_code

from .models import Profile


class SignupForm(forms.Form):
    first_name = forms.CharField(label="نام", max_length=80)
    last_name = forms.CharField(label="نام خانوادگی", max_length=80)
    mobile = forms.CharField(label="شماره موبایل", max_length=11,
                             help_text="یک شماره می‌تواند برای چند نفر (مثلاً اعضای خانواده) استفاده شود.")
    national_code = forms.CharField(
        label="کد ملی", max_length=10, required=False,
        help_text="اگر این شماره قبلاً برای کس دیگری ثبت شده، کد ملی را بنویسید تا نام کاربری شما کد ملی باشد.",
    )
    password1 = forms.CharField(label="رمز عبور", widget=forms.PasswordInput, help_text="حداقل ۵ نویسه.")
    password2 = forms.CharField(label="تکرار رمز عبور", widget=forms.PasswordInput)
    referral_code = forms.CharField(label="کد معرف (اختیاری)", max_length=8, required=False)
    consent = forms.BooleanField(label="قوانین حریم خصوصی و باشگاه مشتریان را می‌پذیرم.")

    def clean_mobile(self):
        mobile = normalize_digits(self.cleaned_data["mobile"])
        validate_mobile(mobile)
        return mobile

    def clean_national_code(self):
        value = normalize_digits(self.cleaned_data.get("national_code") or "").strip()
        if value:
            validate_national_code(value)
        return value

    def clean_referral_code(self):
        code = normalize_digits(self.cleaned_data.get("referral_code", "")).upper()
        if not code:
            return None
        referrer = Profile.objects.filter(referral_code=code).first()
        if not referrer:
            raise forms.ValidationError("کد معرف معتبر نیست.")
        return referrer

    def clean(self):
        cleaned = super().clean()
        User = get_user_model()
        mobile, national_code = cleaned.get("mobile"), cleaned.get("national_code")
        # نام کاربری: موبایل اگر آزاد باشد، وگرنه کد ملی (شماره مشترک خانواده)
        username = ""
        if mobile and not User.objects.filter(username=mobile).exists():
            username = mobile
        elif national_code:
            if User.objects.filter(username=national_code).exists():
                self.add_error("national_code", "با این کد ملی قبلاً ثبت‌نام شده است. از گزینه ورود استفاده کنید.")
            else:
                username = national_code
        elif mobile:
            self.add_error("national_code", "این شماره قبلاً برای کس دیگری ثبت شده؛ کد ملی خودتان را بنویسید تا با آن ثبت‌نام کنید.")
        cleaned["username"] = username
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "دو رمز عبور یکسان نیستند.")
        elif p1:
            user = get_user_model()(
                username=username, first_name=cleaned.get("first_name", ""),
                last_name=cleaned.get("last_name", ""),
            )
            try:
                password_validation.validate_password(p1, user)
            except forms.ValidationError as exc:
                self.add_error("password1", exc)
        return cleaned

    @transaction.atomic
    def save(self):
        data = self.cleaned_data
        user = get_user_model().objects.create_user(
            username=data["username"], password=data["password1"],
            first_name=data["first_name"], last_name=data["last_name"],
        )
        Profile.objects.create(user=user, mobile=data["mobile"], national_code=data["national_code"] or None,
                               referred_by=data["referral_code"])
        return user


class LoginForm(AuthenticationForm):
    username = forms.CharField(label="شماره موبایل یا کد ملی", max_length=150)

    def clean_username(self):
        return normalize_digits(self.cleaned_data["username"])

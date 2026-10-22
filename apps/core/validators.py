import re

from django.core.exceptions import ValidationError

PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def normalize_digits(value):
    """ارقام فارسی و عربی را به ارقام لاتین تبدیل می‌کند."""
    return (value or "").translate(PERSIAN_DIGITS).strip()


def is_valid_national_code(code):
    code = normalize_digits(code)
    if not re.fullmatch(r"\d{10}", code) or len(set(code)) == 1:
        return False
    check = int(code[9])
    total = sum(int(code[i]) * (10 - i) for i in range(9)) % 11
    return check == total if total < 2 else check == 11 - total


def validate_national_code(value):
    if not is_valid_national_code(value):
        raise ValidationError("کد ملی وارد شده معتبر نیست.")


def validate_mobile(value):
    if not re.fullmatch(r"09\d{9}", normalize_digits(value)):
        raise ValidationError("شماره موبایل باید ۱۱ رقم و با ۰۹ شروع شود.")

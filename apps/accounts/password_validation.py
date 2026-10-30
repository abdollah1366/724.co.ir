"""قوانین رمز: برای مراجعان فقط حداقل طول؛ رمز ساده یا هم‌نام با نام کاربری اشکالی ندارد.

برای کارکنان (که به پرونده بیماران دسترسی دارند) رمز تماماً عددی، رایج یا شبیه نام کاربری پذیرفته نمی‌شود.
"""

from django.contrib.auth import password_validation


class StaffOnly:
    """یک اعتبارسنج استاندارد جنگو را فقط برای حساب کارکنان اجرا می‌کند."""

    validator_class = None

    def __init__(self, **options):
        self.inner = self.validator_class(**options)

    def validate(self, password, user=None):
        if user is not None and getattr(user, "is_staff", False):
            self.inner.validate(password, user)

    def get_help_text(self):
        return f"(کارکنان) {self.inner.get_help_text()}"


class StaffUserAttributeSimilarityValidator(StaffOnly):
    validator_class = password_validation.UserAttributeSimilarityValidator


class StaffCommonPasswordValidator(StaffOnly):
    validator_class = password_validation.CommonPasswordValidator


class StaffNumericPasswordValidator(StaffOnly):
    validator_class = password_validation.NumericPasswordValidator

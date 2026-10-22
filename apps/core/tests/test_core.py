import datetime

from django.test import TestCase
from django.urls import reverse

from apps.core.models import ContactMessage
from apps.core.templatetags.persian import fa_digits, jalali, toman
from apps.core.validators import is_valid_national_code, normalize_digits


class ValidatorTests(TestCase):
    def test_valid_national_codes(self):
        for code in ["0499370899", "0013542419", "۰۴۹۹۳۷۰۸۹۹"]:
            self.assertTrue(is_valid_national_code(code), code)

    def test_invalid_national_codes(self):
        for code in ["0499370898", "1111111111", "12345", "abcdefghij", ""]:
            self.assertFalse(is_valid_national_code(code), code)

    def test_normalize_digits(self):
        self.assertEqual(normalize_digits("۰۹۱۲۱۲۳۴۵۶۷"), "09121234567")
        self.assertEqual(normalize_digits("٠٩١٢"), "0912")


class PersianFilterTests(TestCase):
    def test_jalali(self):
        self.assertEqual(jalali(datetime.date(2026, 10, 22)), "پنج‌شنبه ۳۰ مهر ۱۴۰۵")

    def test_toman(self):
        self.assertEqual(toman(2_500_000), "۲۵۰٬۰۰۰ تومان")

    def test_fa_digits(self):
        self.assertEqual(fa_digits("10:30"), "۱۰:۳۰")


class PageTests(TestCase):
    def test_static_pages(self):
        for name in ["core:home", "core:about", "core:privacy", "core:contact"]:
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200, name)
            self.assertContains(response, 'dir="rtl"')

    def test_contact_form_saves_message(self):
        response = self.client.post(
            reverse("core:contact"),
            {"name": "علی", "mobile": "۰۹۱۲۱۲۳۴۵۶۷", "subject": "سؤال", "message": "سلام"},
            follow=True,
        )
        self.assertContains(response, "پیام شما ثبت شد")
        self.assertEqual(ContactMessage.objects.get().mobile, "09121234567")

    def test_contact_form_rejects_bad_mobile(self):
        response = self.client.post(
            reverse("core:contact"), {"name": "علی", "mobile": "123", "subject": "x", "message": "y"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ContactMessage.objects.exists())

    def test_security_headers(self):
        response = self.client.get(reverse("core:home"))
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")

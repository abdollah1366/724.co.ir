"""کد ملی به‌عنوان نام کاربری کارکنان، رمز پیش‌فرض = موبایل با تغییر اجباری، حداقل ۵ نویسه و خروج به صفحه ورود مدیریت."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import StaffAccount
from apps.inventory.tests.fixtures import staff

User = get_user_model()
NC = "0499370899"
MOBILE = "09121234567"


class DefaultPasswordTests(TestCase):
    def setUp(self):
        self.manager = staff("gm", "مدیر کل")
        self.client.force_login(self.manager)

    def create(self, **extra):
        data = {"national_code": "۰۴۹۹۳۷۰۸۹۹", "mobile": "۰۹۱۲۱۲۳۴۵۶۷", "first_name": "زهرا", "last_name": "احمدی",
                "is_active": "on", "roles": ["منشی پزشک"]}
        data.update(extra)
        return self.client.post(reverse("backoffice:staff_new"), data)

    def test_national_code_username_and_mobile_default_password(self):
        self.assertRedirects(self.create(), reverse("backoffice:staff"))
        user = User.objects.get(username=NC)
        self.assertTrue(user.check_password(MOBILE))
        account = user.staff_account
        self.assertEqual((account.national_code, account.mobile, account.must_change_password), (NC, MOBILE, True))
        self.assertContains(self.client.get(reverse("backoffice:staff") + f"?q={NC}"), NC)

    def test_first_login_forces_password_change(self):
        self.create()
        self.client.logout()
        response = self.client.post(reverse("admin:login"), {"username": NC, "password": MOBILE, "next": "/staff/"})
        self.assertRedirects(response, "/staff/", fetch_redirect_response=False)
        self.assertRedirects(self.client.get("/staff/"), reverse("accounts:password_change"))
        self.assertRedirects(self.client.get(reverse("ehr:reception_book")), reverse("accounts:password_change"))
        page = self.client.get(reverse("accounts:password_change"))
        self.assertContains(page, "رمز پیش‌فرض")
        # کمتر از ۵ نویسه یا تماماً عدد پذیرفته نمی‌شود
        for weak in ("ab1", "12345678"):
            r = self.client.post(reverse("accounts:password_change"),
                                 {"old_password": MOBILE, "new_password1": weak, "new_password2": weak})
            self.assertEqual(r.status_code, 200)
        r = self.client.post(reverse("accounts:password_change"),
                             {"old_password": MOBILE, "new_password1": "gul724", "new_password2": "gul724"})
        self.assertRedirects(r, reverse("core:staff_home"), fetch_redirect_response=False)
        self.assertFalse(StaffAccount.objects.get(national_code=NC).must_change_password)
        self.assertEqual(self.client.get("/staff/").status_code, 200)

    def test_custom_username_still_logs_in_with_national_code(self):
        self.create(username="zahra", password="gul724")
        account = StaffAccount.objects.get(national_code=NC)
        self.assertFalse(account.must_change_password)
        self.client.logout()
        self.assertTrue(self.client.login(username=NC, password="gul724"))
        self.client.logout()
        self.assertTrue(self.client.login(username="zahra", password="gul724"))
        self.assertFalse(self.client.login(username=NC, password="wrong-pass"))

    def test_needs_password_or_mobile_and_unique_national_code(self):
        r = self.create(mobile="")
        self.assertContains(r, "رمز پیش‌فرض شود")
        self.create()
        r = self.create(username="other")
        self.assertContains(r, "برای کارمند دیگری ثبت شده")
        self.assertContains(self.create(national_code="1234567890"), "error")

    def test_reset_to_default_password(self):
        self.create(password="gul724")
        user = User.objects.get(username=NC)
        self.client.post(reverse("backoffice:staff_edit", args=[user.pk]), {
            "national_code": NC, "mobile": MOBILE, "first_name": "زهرا", "last_name": "احمدی", "is_active": "on",
            "roles": ["منشی پزشک"], "reset_to_default": "on",
        })
        user.refresh_from_db()
        self.assertTrue(user.check_password(MOBILE))
        self.assertTrue(user.staff_account.must_change_password)

    def test_five_character_minimum(self):
        self.assertRedirects(self.create(username="five", password="ab12c", national_code="", mobile=""),
                             reverse("backoffice:staff"))
        self.assertEqual(self.create(username="four", password="ab1c", national_code="", mobile="").status_code, 200)
        self.assertFalse(User.objects.filter(username="four").exists())


class LogoutRedirectTests(TestCase):
    def test_staff_logout_goes_to_admin_login(self):
        self.client.force_login(staff("gm", "مدیر کل"))
        response = self.client.post(reverse("accounts:logout"))
        self.assertRedirects(response, f"{reverse('admin:login')}?next={reverse('core:staff_home')}",
                             fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_django_admin_logout_goes_to_admin_login(self):
        self.client.force_login(staff("gm", "مدیر کل"))
        response = self.client.post(reverse("admin:logout"))
        self.assertTrue(response["Location"].startswith(reverse("admin:login")))

    def test_customer_logout_goes_home(self):
        user = User.objects.create_user("09120000000", password="gul724x")
        self.client.force_login(user)
        self.assertRedirects(self.client.post(reverse("accounts:logout")), reverse("core:home"), fetch_redirect_response=False)

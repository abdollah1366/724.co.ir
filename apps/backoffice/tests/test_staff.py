from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.audit.roles import ROLE_AREAS, ROLE_DESCRIPTIONS, ROLES
from apps.ehr.tests.test_ehr import doctor
from apps.inventory.tests.fixtures import staff

User = get_user_model()
PASSWORD = "Strong-pass-724!"


class RoleCatalogTests(TestCase):
    def test_every_role_is_listed_and_described(self):
        listed = [name for _, names in ROLE_AREAS for name in names]
        self.assertEqual(sorted(listed), sorted(ROLES))
        self.assertEqual(set(ROLE_DESCRIPTIONS), set(ROLES))

    def test_general_manager_and_secretary_have_no_clinical_access(self):
        manager = staff("gm", "مدیر کل")
        secretary = staff("sec", "منشی پزشک")
        for user in (manager, secretary):
            self.assertFalse(user.has_perm("ehr.view_clinical_record"))
            self.assertFalse(user.has_perm("lab.view_labresult"))
            self.assertTrue(user.has_perm("appointments.add_appointment"))
            self.assertTrue(user.has_perm("ehr.add_clinicticket"))
        self.assertTrue(manager.has_perm("accounting.view_financial_reports"))
        self.assertTrue(manager.has_perm("ehr.view_management_report"))
        self.assertTrue(manager.has_perm("auth.add_user"))
        self.client.force_login(manager)
        self.assertEqual(self.client.get(reverse("ehr:report")).status_code, 200)
        self.assertEqual(self.client.get(reverse("ehr:my_patients")).status_code, 403)
        self.assertEqual(self.client.get(reverse("ehr:pharmacy_queue")).status_code, 403)


class StaffPageTests(TestCase):
    def setUp(self):
        self.manager = staff("gm", "مدیر کل")
        self.client.force_login(self.manager)

    def test_create_staff_with_roles_and_doctor_link(self):
        doc = doctor()
        page = self.client.get(reverse("backoffice:staff_new"))
        self.assertContains(page, "انباردار")
        self.assertNotContains(page, 'value="پزشک"')  # نقش بالینی را فقط مدیر ارشد می‌دهد
        response = self.client.post(reverse("backoffice:staff_new"), {
            "username": "ali", "first_name": "علی", "last_name": "رضایی", "password": PASSWORD, "is_active": "on",
            "roles": ["انباردار", "صندوق‌دار"], "doctor": doc.pk,
        })
        self.assertRedirects(response, reverse("backoffice:staff"))
        user = User.objects.get(username="ali")
        self.assertTrue(user.is_staff and user.check_password(PASSWORD))
        self.assertEqual(sorted(user.groups.values_list("name", flat=True)), ["انباردار", "صندوق‌دار"])
        doc.refresh_from_db()
        self.assertEqual(doc.user, user)
        self.assertTrue(AuditLog.objects.filter(message__contains="ساخت حساب کارمند").exists())
        listing = self.client.get(reverse("backoffice:staff") + "?role=انباردار")
        self.assertContains(listing, "ali")

    def test_cannot_escalate_or_touch_superuser(self):
        target = staff("doc1", "پزشک", "انباردار")
        self.client.post(reverse("backoffice:staff_edit", args=[target.pk]), {
            "first_name": "x", "last_name": "y", "is_active": "on", "roles": ["مسئول فنی درمانگاه"],
        })
        self.assertEqual(sorted(target.groups.values_list("name", flat=True)), ["انباردار", "پزشک"])
        self.client.post(reverse("backoffice:staff_edit", args=[target.pk]), {
            "first_name": "x", "last_name": "y", "is_active": "on", "roles": [],
        })
        self.assertEqual(list(target.groups.values_list("name", flat=True)), ["پزشک"])  # نقش بالینی دست‌نخورده
        root = User.objects.create_superuser("root", password=PASSWORD)
        root.is_staff = True
        root.save()
        self.assertEqual(self.client.get(reverse("backoffice:staff_edit", args=[root.pk])).status_code, 403)

    def test_password_rules_and_self_deactivation(self):
        r = self.client.post(reverse("backoffice:staff_new"), {"username": "weak", "first_name": "a", "last_name": "b",
                                                              "password": "123", "is_active": "on"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(User.objects.filter(username="weak").exists())
        self.client.post(reverse("backoffice:staff_edit", args=[self.manager.pk]), {"first_name": "a", "last_name": "b"})
        self.manager.refresh_from_db()
        self.assertTrue(self.manager.is_active)

    def test_other_roles_cannot_open_staff_page(self):
        self.client.force_login(staff("cashier", "صندوق‌دار"))
        self.assertEqual(self.client.get(reverse("backoffice:staff")).status_code, 403)
        self.assertEqual(self.client.get(reverse("backoffice:staff_new")).status_code, 403)

    def test_manager_cannot_reset_password_of_clinical_account(self):
        target = staff("doc2", "پزشک")
        target.set_password(PASSWORD)
        target.save()
        self.assertContains(self.client.get(reverse("backoffice:staff_edit", args=[target.pk])), "فقط مدیر ارشد")
        self.client.post(reverse("backoffice:staff_edit", args=[target.pk]), {
            "first_name": "x", "last_name": "y", "is_active": "on", "password": "Another-pass-724!",
        })
        target.refresh_from_db()
        self.assertTrue(target.check_password(PASSWORD))
        # حسابی که همه نقش‌هایش در اختیار مدیر است، رمزش قابل تغییر است.
        clerk = staff("clerk", "انباردار")
        self.client.post(reverse("backoffice:staff_edit", args=[clerk.pk]), {
            "first_name": "x", "last_name": "y", "is_active": "on", "password": "Another-pass-724!", "roles": ["انباردار"],
        })
        clerk.refresh_from_db()
        self.assertTrue(clerk.check_password("Another-pass-724!"))

    def test_admin_user_pages_are_superuser_only(self):
        target = staff("clerk2", "انباردار")
        self.assertEqual(self.client.get(reverse("admin:auth_user_change", args=[target.pk])).status_code, 200)  # فقط مشاهده
        response = self.client.post(reverse("admin:auth_user_change", args=[target.pk]), {
            "username": "clerk2", "is_superuser": "on", "is_staff": "on", "is_active": "on",
        })
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get(reverse("admin:auth_user_password_change", args=[target.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("admin:auth_user_change", args=[self.manager.pk])).status_code, 200)
        self.manager.refresh_from_db()
        target.refresh_from_db()
        self.assertFalse(self.manager.is_superuser or target.is_superuser)


class BackofficeScopeTests(TestCase):
    def test_non_pharmacy_staff_do_not_see_sales_or_stock(self):
        self.client.force_login(staff("sec", "منشی پزشک"))
        home = self.client.get(reverse("backoffice:dashboard"))
        self.assertEqual(home.status_code, 200)
        self.assertNotContains(home, reverse("backoffice:stock"))
        for name in ("alerts", "stock", "api_dashboard"):
            self.assertEqual(self.client.get(reverse(f"backoffice:{name}")).status_code, 403, name)

from decimal import Decimal
from io import StringIO


from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.lab.models import LabPanel, LabTest, ScreeningRule


class SeedMixin:
    @classmethod
    def setUpTestData(cls):
        call_command("seed_lab", "--demo", stdout=StringIO())


class CatalogTests(SeedMixin, TestCase):
    def test_seed_is_idempotent_and_keeps_admin_edits(self):
        LabTest.objects.filter(code="FBS").update(price=123)
        count = LabTest.objects.count()
        call_command("seed_lab", stdout=StringIO())
        self.assertEqual(LabTest.objects.count(), count)
        self.assertEqual(LabTest.objects.get(code="FBS").price, 123)

    def test_reference_range_prefers_sex_specific(self):
        hgb = LabTest.objects.get(code="HGB")
        self.assertEqual(hgb.reference_range_for("m", 40 * 365).low, Decimal("13.5"))
        self.assertEqual(hgb.reference_range_for("f", 40 * 365).low, Decimal("12.0"))
        self.assertEqual(LabTest.objects.get(code="WBC").reference_range_for("f", 3000).display, "4 - 10")
        self.assertIsNone(LabTest.objects.get(code="PSA").reference_range_for("f", 3000))

    def test_panel_price_uses_package_or_sum(self):
        panel = LabPanel.objects.get(code="LIPID")
        self.assertEqual(panel.price, 2_300_000)
        panel.package_price = None
        self.assertEqual(panel.price, sum(t.price for t in panel.tests.all()))

    def test_fasting_hours_from_preparations(self):
        self.assertEqual(LabTest.objects.get(code="TG").fasting_hours, 12)
        self.assertIsNone(LabTest.objects.get(code="WBC").fasting_hours)


class PublicPageTests(SeedMixin, TestCase):
    def test_home_list_detail_and_panel(self):
        self.assertContains(self.client.get(reverse("lab:home")), "چکاپ عمومی")
        self.assertContains(self.client.get(reverse("lab:test_list"), {"q": "قند"}), "قند خون ناشتا")
        self.assertContains(self.client.get(reverse("lab:test_list"), {"q": "fbs"}), "قند خون ناشتا")
        self.assertContains(self.client.get(reverse("lab:test_list"), {"q": "zzz"}), "آزمایشی پیدا نشد")
        self.assertContains(self.client.get(reverse("lab:test_list"), {"q": "t۴"}), "تیروکسین")
        detail = self.client.get(reverse("lab:test_detail", args=["tg"]))
        self.assertContains(detail, "ناشتایی ۱۲ ساعته")
        self.assertContains(self.client.get(reverse("lab:panel_detail", args=["checkup"])), "۱۲ ساعت ناشتایی")

    def test_hidden_test_not_public(self):
        LabTest.objects.filter(code="ALP").update(is_public=False)
        self.assertEqual(self.client.get(reverse("lab:test_detail", args=["alp"])).status_code, 404)

    def test_combined_preparation(self):
        response = self.client.get(reverse("lab:preparation"), {"t": ["WBC", "TG", "PSA"]})
        self.assertContains(response, "۱۲ ساعت ناشتایی")
        self.assertContains(response, "آمادگی PSA")

    def test_screening_guide_filters_and_never_stores(self):
        url = reverse("lab:screening_guide")
        response = self.client.get(url, {"age": "۴۰", "sex": "m", "risks": ["diabetes"]})
        rules = {r.title for r in response.context["rules"]}
        self.assertIn("بررسی دیابت", rules)
        self.assertIn("پیگیری بیماران دیابتی", rules)
        self.assertNotIn("آزمایش‌های بارداری", rules)
        self.assertNotIn("سرطان روده بزرگ", rules)
        self.assertEqual(response["Cache-Control"], "no-store")
        young = {r.title for r in self.client.get(url, {"age": "25", "sex": "f"}).context["rules"]}
        self.assertEqual(young, {"چربی خون", "هپاتیت C"})

    def test_unapproved_rules_hidden(self):
        ScreeningRule.objects.update(is_approved=False)
        response = self.client.get(reverse("lab:screening_guide"), {"age": "50", "sex": "m"})
        self.assertEqual(response.context["rules"], [])

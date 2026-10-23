from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase

from apps.lab.models import LabOrder


class LabDemoTests(TestCase):
    def test_full_flow_balances(self):
        out = StringIO()
        call_command("lab_demo", stdout=out)
        text = out.getvalue()
        self.assertIn("همه کنترل‌ها درست است", text)
        order = LabOrder.objects.get()
        self.assertEqual(order.progress, LabOrder.Progress.READY)
        self.assertEqual(order.gross_amount, order.basic_share + order.supplementary_share + order.patient_share)
        self.assertEqual(order.balance_due, 0)
        with self.assertRaises(CommandError):
            call_command("lab_demo", stdout=StringIO())

    def test_reports_page(self):
        call_command("lab_demo", stdout=StringIO())
        from django.contrib.auth import get_user_model
        manager = get_user_model().objects.get(username="demo-lab-manager")
        self.client.force_login(manager)
        page = self.client.get("/lab/staff/reports/")
        self.assertContains(page, "زمان جواب‌دهی")
        self.assertContains(page, "دانا تکمیلی کارکنان")
        self.assertContains(page, "HBA1C")
        consultant = get_user_model().objects.get(username="demo-lab-consultant")
        self.client.force_login(consultant)
        self.assertEqual(self.client.get("/lab/staff/reports/").status_code, 200)
        self.assertNotContains(self.client.get("/lab/staff/reports/"), "سهم بیمار")

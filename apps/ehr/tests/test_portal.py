from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.core import sms
from apps.ehr import demo
from apps.ehr.models import Patient, PortalCode
from apps.lab.tests.fixtures import staff


class PortalTests(TestCase):
    def setUp(self):
        sms.outbox.clear()
        self.result = demo.run()
        self.patient = self.result["patient"]
        self.user = get_user_model().objects.create_user("patient1", password="x-strong-pass-1")

    def link(self, identifier=None, user=None):
        self.client.force_login(user or self.user)
        self.client.post(reverse("ehr:portal_link"), {"identifier": identifier or self.patient.national_code})
        return sms.outbox[-1]["text"].split(":")[1].split()[0] if sms.outbox else None

    def test_link_with_otp_then_view_summary(self):
        self.client.force_login(self.user)
        self.assertRedirects(self.client.get(reverse("ehr:my_record")), reverse("ehr:portal_link"))
        code = self.link()
        self.assertEqual(sms.outbox[-1]["mobile"], "09120000001")
        self.client.post(reverse("ehr:portal_link"), {"code": "000000" if code != "000000" else "111111"})  # کد اشتباه
        self.assertIsNone(Patient.objects.get(pk=self.patient.pk).user)
        self.client.post(reverse("ehr:portal_link"), {"code": code})
        self.assertEqual(Patient.objects.get(pk=self.patient.pk).user, self.user)
        page = self.client.get(reverse("ehr:my_record"))
        self.assertContains(page, "Metformin")
        self.assertContains(page, "Penicillin")
        self.assertContains(page, "تمرین پل")
        self.assertContains(page, "M۰۲")  # نوبت حضوری امروز
        self.assertContains(page, "نوبت شماست")
        self.assertNotContains(page, "یادداشت محرمانه نمونه")  # متن جلسه روانشناسی در خلاصه نمی‌آید
        self.assertIn("no-store", page["Cache-Control"])

    def test_unknown_identifier_same_message_and_no_sms(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("ehr:portal_link"), {"identifier": "0000000000"}, follow=True)
        self.assertContains(response, "اگر پرونده تأییدشده‌ای")
        self.assertEqual(sms.outbox, [])
        self.assertFalse(PortalCode.objects.exists())

    def test_code_bound_to_requesting_account(self):
        code = self.link()
        other = get_user_model().objects.create_user("other", password="x-strong-pass-1")
        self.client.force_login(other)
        session = self.client.session
        session["ehr_portal_code"] = PortalCode.objects.get().pk
        session.save()
        self.client.post(reverse("ehr:portal_link"), {"code": code})
        self.assertIsNone(Patient.objects.get(pk=self.patient.pk).user)

    def test_report_and_demo_guard(self):
        head = self.result["users"]["head"]
        self.client.force_login(head)
        page = self.client.get(reverse("ehr:report"))
        self.assertContains(page, "E11.9")
        self.assertContains(page, "فیزیوتراپی")
        self.assertNotContains(page, "زهرا")
        self.assertEqual(page.context["report"]["encounters"], 1)
        doctor = staff("plain-doc", "پزشک")
        self.client.force_login(doctor)
        self.assertEqual(self.client.get(reverse("ehr:report")).status_code, 403)
        with self.assertRaises(Exception):
            call_command("ehr_demo", stdout=StringIO())


class DemoCommandTests(TestCase):
    def test_command_runs_and_checks(self):
        out = StringIO()
        call_command("ehr_demo", stdout=out)
        self.assertIn("همه کنترل‌ها درست است", out.getvalue())

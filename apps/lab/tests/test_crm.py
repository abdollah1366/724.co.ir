import datetime
from io import StringIO

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core import sms
from apps.lab.models import ConsultRequest, Feedback, Interaction, LabOrder, RecallReminder, SamplingAppointment
from apps.lab.services import crm, lis

from .fixtures import NATIONAL_CODE, make_order, patient, seed, staff
from .test_lis import collect_all, result


class ConsultTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed()

    def setUp(self):
        sms.outbox.clear()
        cache.clear()

    def _request(self, **extra):
        data = {"topic": "price", "full_name": "علی احمدی", "mobile": "۰۹۱۲۱۱۱۲۲۳۳", "preferred_time": "any",
                "message": "قیمت چکاپ با بیمه دانا؟", "consent": "on", **extra}
        return self.client.post(reverse("lab:consult"), data)

    def test_public_request_requires_consent_and_sends_tracking_sms(self):
        self.assertContains(self.client.get(reverse("lab:consult")), "مشاور آزمایشگاه")
        self.assertContains(self._request(consent=""), "این فیلد لازم است")
        response = self._request()
        self.assertRedirects(response, reverse("lab:consult_done"))
        consult = ConsultRequest.objects.get()
        self.assertEqual((consult.mobile, consult.status, consult.channel), ("09121112233", "new", "web"))
        self.assertIsNotNone(consult.consent_at)
        self.assertContains(self.client.get(response.url), consult.tracking_code)
        self.assertIn(consult.tracking_code, sms.outbox[-1]["text"])
        self.assertNotIn("چکاپ", sms.outbox[-1]["text"])

    def test_rate_limit(self):
        for _ in range(5):
            self._request()
        self._request()
        self.assertEqual(ConsultRequest.objects.count(), 5)

    def test_staff_inbox_detail_log_and_link(self):
        consultant = staff("moshaver", "مشاور آزمایشگاه")
        self._request()
        consult = ConsultRequest.objects.get()
        self.client.force_login(consultant)
        page = self.client.get(reverse("lab:crm_inbox"))
        self.assertContains(page, "علی احمدی")
        self.assertContains(self.client.get(reverse("lab:crm_inbox"), {"view": "unassigned"}), "علی احمدی")
        self.assertContains(self.client.get(reverse("lab:crm_detail", args=[consult.pk])), "ثبت پیگیری")
        self.client.post(reverse("lab:crm_action", args=[consult.pk]), {"action": "assign", "user": consultant.pk})
        self.client.post(reverse("lab:crm_action", args=[consult.pk]), {
            "action": "log", "kind": "call_out", "text": "تعرفه و سهم بیمه توضیح داده شد", "status": "resolved",
        })
        consult.refresh_from_db()
        self.assertEqual((consult.status, consult.assigned_to), ("resolved", consultant))
        self.assertIsNotNone(consult.resolved_at)
        self.assertEqual(Interaction.objects.count(), 1)
        # پذیرش با موبایل دیگر متصل نمی‌شود
        reception = staff("rec", "پذیرش آزمایشگاه")
        order = make_order(reception, patient(), ["FBS"])
        self.client.post(reverse("lab:crm_action", args=[consult.pk]), {"action": "link", "number": order.number})
        consult.refresh_from_db()
        self.assertIsNone(consult.order)
        # ثبت تماس تلفنی توسط کارشناس
        self.client.post(reverse("lab:crm_inbox"), {
            "topic": "home", "full_name": "زهرا", "mobile": order.patient.mobile, "preferred_time": "any", "channel": "phone",
            "consent": "on",
        })
        phone = ConsultRequest.objects.get(channel="phone")
        self.client.post(reverse("lab:crm_action", args=[phone.pk]), {"action": "link", "number": order.number})
        phone.refresh_from_db()
        self.assertEqual(phone.patient, order.patient)
        self.assertContains(self.client.get(reverse("lab:crm_detail", args=[phone.pk])), order.number)

    def test_consultant_cannot_see_results(self):
        consultant = staff("moshaver", "مشاور آزمایشگاه")
        reception = staff("rec", "پذیرش آزمایشگاه")
        order = make_order(reception, patient(), ["FBS"])
        self.client.force_login(consultant)
        self.assertEqual(self.client.get(reverse("lab:staff_results", args=[order.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("lab:staff_report", args=[order.pk])).status_code, 403)


class FeedbackAndReminderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed()
        cls.tech = staff("tech", "مسئول فنی آزمایشگاه")
        cls.reception = staff("rec", "پذیرش آزمایشگاه")

    def setUp(self):
        sms.outbox.clear()

    def _ready_order(self, codes=("HBA1C",), **fields):
        order = make_order(self.reception, self.patient, list(codes), **fields)
        collect_all(order, self.tech)
        for code in codes:
            lis.approve_result(lis.enter_result(result(order, code), "5.2", self.tech), self.tech)
        order.refresh_from_db()
        return order

    def setUpPatient(self, consent=True):
        self.patient = patient(reminder_consent_at=timezone.now() if consent else None)

    def test_feedback_flow_and_low_score_creates_follow_up(self):
        self.setUpPatient()
        yesterday = timezone.localdate() - datetime.timedelta(days=1)
        order = self._ready_order(date=yesterday)
        self.assertEqual(order.progress, LabOrder.Progress.READY)
        out = StringIO()
        call_command("lab_reminders", stdout=out)
        feedback = Feedback.objects.get(order=order)
        self.assertIn(feedback.token, sms.outbox[-1]["text"])
        self.assertNotIn("HBA1C", sms.outbox[-1]["text"])
        url = reverse("lab:feedback", args=[feedback.token])
        self.assertContains(self.client.get(url), "رضایت کلی")
        self.client.post(url, {"overall": "2", "waiting": "1", "comment": "صف شلوغ بود", "wants_contact": "on"})
        feedback.refresh_from_db()
        self.assertEqual(feedback.overall, 2)
        follow_up = ConsultRequest.objects.get()
        self.assertEqual((follow_up.topic, follow_up.channel, follow_up.order), ("complaint", "feedback", order))
        self.assertContains(self.client.get(url), "قبلاً ثبت شده")
        # اجرای دوباره، نظرسنجی تکراری نمی‌فرستد
        count = len(sms.outbox)
        call_command("lab_reminders", stdout=StringIO())
        self.assertEqual(len(sms.outbox), count)
        manager = staff("boss", "مدیر آزمایشگاه")
        self.client.force_login(manager)
        self.assertContains(self.client.get(reverse("lab:crm_feedback")), "صف شلوغ بود")

    def test_recall_only_with_consent_and_without_newer_order(self):
        self.setUpPatient(consent=False)
        old = timezone.localdate() - datetime.timedelta(days=100)
        self._ready_order(date=old)
        self.assertEqual(crm.due_recalls(timezone.localdate()), [])
        self.patient.reminder_consent_at = timezone.now()
        self.patient.save()
        due = crm.due_recalls(timezone.localdate())
        self.assertEqual(len(due), 1)
        self.assertEqual(crm.send_recalls(timezone.localdate()), 1)
        self.assertNotIn("HBA1C", sms.outbox[-1]["text"])
        self.assertEqual(RecallReminder.objects.count(), 1)
        self.assertEqual(crm.due_recalls(timezone.localdate()), [])

    def test_recall_skipped_when_repeated(self):
        self.setUpPatient()
        self._ready_order(date=timezone.localdate() - datetime.timedelta(days=100))
        make_order(self.reception, self.patient, ["HBA1C"], date=timezone.localdate() - datetime.timedelta(days=5))
        self.assertEqual(crm.due_recalls(timezone.localdate()), [])

    def test_appointment_reminder_once(self):
        self.setUpPatient()
        tomorrow = timezone.localdate() + datetime.timedelta(days=1)
        SamplingAppointment.objects.create(date=tomorrow, time=datetime.time(8), full_name="x", national_code=NATIONAL_CODE,
                                           mobile="09121234567", consent_at=timezone.now())
        self.assertEqual(crm.send_appointment_reminders(tomorrow), 1)
        self.assertEqual(crm.send_appointment_reminders(tomorrow), 0)

    def test_add_months(self):
        self.assertEqual(crm.add_months(datetime.date(2026, 1, 31), 1), datetime.date(2026, 2, 28))
        self.assertEqual(crm.add_months(datetime.date(2026, 11, 15), 3), datetime.date(2027, 2, 15))

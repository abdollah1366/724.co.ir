import json

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.lab.models import QueueCounter, QueueService, QueueTicket, SamplingAppointment
from apps.lab.services import queue

from .fixtures import NATIONAL_CODE, seed, staff


class QueueTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed()
        cls.reception = QueueService.objects.get(code="A")
        cls.sampling = QueueService.objects.get(code="B")
        cls.desk = QueueCounter.objects.get(name="باجه ۲")
        cls.room = QueueCounter.objects.get(name="اتاق نمونه‌گیری ۱")
        cls.user = staff("desk", "پذیرش آزمایشگاه")

    def test_daily_numbering_and_priority_order(self):
        first = queue.issue_ticket(self.reception)
        second = queue.issue_ticket(self.reception)
        elder = queue.issue_ticket(self.reception, QueueTicket.Priority.SPECIAL)
        self.assertEqual([first.label, second.label, elder.label], ["A001", "A002", "A003"])
        self.assertEqual(queue.issue_ticket(self.sampling).label, "B001")
        self.assertEqual(queue.call_next(self.desk, self.user), elder)
        # فراخوان بعدی، نوبت قبلی همین باجه را می‌بندد
        self.assertEqual(queue.call_next(self.desk, self.user), first)
        elder.refresh_from_db()
        self.assertEqual(elder.status, "done")
        self.assertEqual(queue.current_for(self.desk), first)
        # باجه پذیرش، صف نمونه‌گیری را فرا نمی‌خواند
        queue.call_next(self.desk, self.user)
        self.assertIsNone(queue.call_next(self.desk, self.user))

    def test_recall_skip_transfer(self):
        ticket = queue.issue_ticket(self.reception)
        queue.call_next(self.desk, self.user)
        ticket.refresh_from_db()
        queue.recall(ticket)
        self.assertEqual(ticket.recall_count, 1)
        queue.transfer(ticket, self.sampling)
        ticket.refresh_from_db()
        self.assertEqual((ticket.service, ticket.status, ticket.label), (self.sampling, "waiting", "B001"))
        with self.assertRaises(ValidationError):
            queue.finish(ticket)
        ticket = queue.call_next(self.room, self.user)
        self.assertEqual(ticket.label, "B001")
        queue.skip(ticket)
        queue.call_ticket(ticket, self.room, self.user)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, "called")

    def test_check_in_with_tracking_code(self):
        appointment = SamplingAppointment.objects.create(
            date=timezone.localdate(), time=timezone.localtime().time(), full_name="x", national_code=NATIONAL_CODE,
            mobile="09120000000", consent_at=timezone.now(),
        )
        walk_in = queue.issue_ticket(self.reception)
        booked = queue.check_in(appointment.tracking_code.lower(), self.reception)
        self.assertEqual(booked.priority, QueueTicket.Priority.APPOINTMENT)
        self.assertEqual(queue.check_in(appointment.tracking_code, self.reception), booked)
        self.assertEqual(queue.call_next(self.desk, self.user), booked)
        self.assertEqual(queue.call_next(self.desk, self.user), walk_in)
        with self.assertRaises(ValidationError):
            queue.check_in("NOPE1234", self.reception)

    def test_display_data_has_no_personal_data(self):
        appointment = SamplingAppointment.objects.create(
            date=timezone.localdate(), time=timezone.localtime().time(), full_name="مریم رضایی", national_code=NATIONAL_CODE,
            mobile="09121234567", consent_at=timezone.now(),
        )
        queue.check_in(appointment.tracking_code, self.reception)
        queue.issue_ticket(self.reception)
        queue.call_next(self.desk, self.user)
        response = self.client.get(reverse("lab:queue_display_data"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        for private in ("مریم", NATIONAL_CODE, "0912"):
            self.assertNotIn(private, body)
        data = json.loads(body)
        self.assertEqual(data["calls"][0]["ticket"], "A001")
        self.assertEqual(data["calls"][0]["counter"], "باجه ۲")
        self.assertEqual(data["waiting"][0]["count"], 1)
        only_b = json.loads(self.client.get(reverse("lab:queue_display_data"), {"s": "b"}).content)
        self.assertEqual(only_b["calls"], [])
        self.assertContains(self.client.get(reverse("lab:queue_display")), "نمایشگر")

    def test_kiosk_and_console_views(self):
        kiosk_user = staff("kiosk", "کیوسک نوبت")
        self.client.force_login(kiosk_user)
        response = self.client.post(reverse("lab:kiosk"), {"service": self.reception.pk})
        ticket = QueueTicket.objects.get()
        self.assertRedirects(response, reverse("lab:kiosk_ticket", args=[ticket.pk]))
        self.assertContains(self.client.get(response.url), "A۰۰۱")
        self.client.post(reverse("lab:kiosk"), {"service": self.reception.pk, "special": "1"})
        self.assertEqual(QueueTicket.objects.filter(priority=2).count(), 1)
        # کاربر کیوسک به کنسول فراخوان دسترسی ندارد
        self.assertEqual(self.client.get(reverse("lab:queue_console")).status_code, 403)

        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse("lab:queue_console")), "باجه من")
        page = self.client.get(reverse("lab:queue_console"), {"counter": self.desk.pk})
        self.assertContains(page, "فراخوان نفر بعد")
        self.client.post(reverse("lab:queue_action"), {"action": "next"})
        self.assertEqual(queue.current_for(self.desk).priority, 2)
        self.client.post(reverse("lab:queue_action"), {"action": "transfer", "ticket": queue.current_for(self.desk).pk,
                                                       "service": self.sampling.pk})
        self.assertEqual(QueueTicket.objects.filter(service=self.sampling, status="waiting").count(), 1)
        self.client.post(reverse("lab:queue_action"), {"action": "issue", "service": self.reception.pk})
        self.assertEqual(QueueTicket.objects.filter(service=self.reception).count(), 2)

    def test_stats(self):
        queue.issue_ticket(self.reception)
        queue.call_next(self.desk, self.user)
        queue.issue_ticket(self.reception)
        row = queue.stats(timezone.localdate())[0]
        self.assertEqual((row["total"], row["waiting"], row["avg_wait"]), (2, 1, 0))

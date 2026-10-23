import datetime
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core import sms
from apps.core.models import SmsMessage
from apps.ehr import clinical, engagement
from apps.ehr.models import ClinicTicket, Encounter, Prescription, PrescriptionItem, Reminder
from apps.lab.tests.fixtures import staff

from .test_ehr import appointment, doctor


class ReminderTests(TestCase):
    def setUp(self):
        sms.outbox.clear()
        self.user = staff("doc", "پزشک")
        self.doc = doctor(self.user)
        self.appt = appointment(self.doc)
        self.patient = self.appt.patient
        self.patient.first_name = "مریم"
        self.patient.save()
        self.today = timezone.localdate()

    def consent(self):
        self.patient.reminder_consent_at = timezone.now()
        self.patient.save()

    def test_signed_encounter_with_next_visit_creates_reminder(self):
        encounter = clinical.start_encounter(self.patient, self.doc, self.user, self.appt, Encounter.Kind.VISIT)
        encounter.chief_complaint = "پیگیری فشار خون"
        encounter.next_visit = self.today + datetime.timedelta(days=30)
        encounter.save()
        clinical.sign(encounter, self.user)
        reminder = Reminder.objects.get()
        self.assertEqual((reminder.kind, reminder.due_date, reminder.send_on),
                         ("visit", encounter.next_visit, encounter.next_visit - datetime.timedelta(days=1)))

    def test_sms_only_with_consent_and_no_medical_content(self):
        engagement.create_reminder(self.patient, "refill", self.today + datetime.timedelta(days=1), self.user, note="متفورمین")
        self.assertEqual(engagement.send_reminders(), {"sent": 0, "no_consent": 1, "failed": 0})
        self.assertEqual(sms.outbox, [])
        self.consent()
        r = engagement.create_reminder(self.patient, "refill", self.today + datetime.timedelta(days=1), self.user, note="متفورمین")
        engagement.create_reminder(self.patient, "visit", self.today + datetime.timedelta(days=1), self.user)
        self.assertEqual(engagement.send_reminders()["sent"], 1)  # یک پیامک برای دو یادآور همان روز
        text = sms.outbox[0]["text"]
        self.assertIn("مریم عزیز", text)
        for secret in ("متفورمین", "دارو", self.doc.last_name, "فشار"):
            self.assertNotIn(secret, text)
        r.refresh_from_db()
        self.assertEqual(r.status, "sent")
        self.assertEqual(engagement.send_reminders()["sent"], 0)

    def test_reminder_form_and_cancel_from_chart(self):
        self.client.force_login(self.user)
        url = reverse("ehr:reminder_add", args=[self.patient.pk])
        self.client.post(url, {"rem-kind": "lab", "rem-due_date": "1300/01/01", "rem-days_before": "1"})
        self.assertFalse(Reminder.objects.exists())
        due = self.today + datetime.timedelta(days=10)
        import jdatetime
        self.client.post(url, {"rem-kind": "lab", "rem-due_date": jdatetime.date.fromgregorian(date=due).strftime("%Y/%m/%d"),
                               "rem-days_before": "۲", "rem-note": "HbA1c"})
        reminder = Reminder.objects.get()
        self.assertEqual(reminder.send_on, due - datetime.timedelta(days=2))
        chart = self.client.get(reverse("ehr:chart", args=[self.patient.pk]))
        self.assertContains(chart, "رضایت پیامکی نداده")
        self.client.post(reverse("ehr:reminder_cancel", args=[reminder.pk]))
        reminder.refresh_from_db()
        self.assertEqual(reminder.status, "cancelled")

    def test_refill_reminder_from_prescription(self):
        from apps.ehr.models import GenericDrug

        generic = GenericDrug.objects.create(name="Metformin", dosage_form="Tablet", strength="500 mg")
        rx = Prescription.objects.create(patient=self.patient, prescriber=self.doc, created_by=self.user)
        PrescriptionItem.objects.create(prescription=rx, generic=generic, quantity=60, duration_days=30)
        rx.issue(self.user)
        self.client.force_login(self.user)
        self.client.post(reverse("ehr:refill_reminder", args=[rx.pk]))
        reminder = Reminder.objects.get()
        self.assertEqual((reminder.kind, reminder.due_date, reminder.send_on),
                         ("refill", self.today + datetime.timedelta(days=30), self.today + datetime.timedelta(days=27)))
        with self.assertRaises(ValidationError):
            engagement.refill_reminder(rx, self.user)

    def test_patient_form_consent(self):
        head = staff("head", "مسئول فنی درمانگاه")
        self.client.force_login(head)
        data = {"national_code": self.patient.national_code, "first_name": "مریم", "last_name": "رضایی", "sex": "f",
                "mobile": "09121234567", "reminder_consent": "on"}
        self.client.post(reverse("ehr:patient_edit", args=[self.patient.pk]), data)
        self.patient.refresh_from_db()
        self.assertIsNotNone(self.patient.reminder_consent_at)
        del data["reminder_consent"]
        self.client.post(reverse("ehr:patient_edit", args=[self.patient.pk]), data)
        self.patient.refresh_from_db()
        self.assertIsNone(self.patient.reminder_consent_at)

    def test_command_and_appointment_reminders(self):
        from apps.appointments.models import Appointment

        tomorrow = Appointment.objects.create(doctor=self.doc, date=self.today + datetime.timedelta(days=1),
                                              time=datetime.time(9, 30), patient_full_name="رضا احمدی",
                                              national_code="0499370899", mobile="09120000000", consent_at=timezone.now())
        call_command("ehr_reminders", "--dry-run", stdout=StringIO())
        self.assertEqual(sms.outbox, [])
        call_command("ehr_reminders", stdout=StringIO())
        self.assertEqual(len(sms.outbox), 1)
        self.assertIn(tomorrow.tracking_code, sms.outbox[0]["text"])
        self.assertNotIn(self.doc.last_name, sms.outbox[0]["text"])
        call_command("ehr_reminders", stdout=StringIO())
        self.assertEqual(len(sms.outbox), 1)
        self.assertEqual(SmsMessage.objects.filter(kind="clinic_reminder").count(), 1)


class ClinicQueueTests(TestCase):
    def setUp(self):
        self.user = staff("doc", "پزشک")
        self.doc = doctor(self.user)
        self.doc.room, self.doc.queue_code = "اتاق ۳", "M"
        self.doc.save()
        self.reception = staff("rec", "پذیرش درمانگاه")
        self.appt = appointment(self.doc)

    def test_check_in_call_and_display_without_names(self):
        self.client.force_login(self.reception)
        page = self.client.get(reverse("ehr:queue_reception"))
        self.assertContains(page, "مریم رضایی")
        r = self.client.post(reverse("ehr:queue_reception"), {"action": "check_in", "appointment": self.appt.pk})
        ticket = ClinicTicket.objects.get()
        self.assertRedirects(r, reverse("ehr:queue_ticket", args=[ticket.pk]))
        self.assertEqual((ticket.label, ticket.patient, ticket.number), ("M01", self.appt.patient, 1))
        # حضور دوباره همان نوبت شماره جدید نمی‌دهد
        self.client.post(reverse("ehr:queue_reception"), {"action": "check_in", "appointment": self.appt.pk})
        self.client.post(reverse("ehr:queue_reception"), {"action": "walk_in", "w-doctor": self.doc.pk, "w-special": "on"})
        self.assertEqual(ClinicTicket.objects.count(), 2)
        special = ClinicTicket.objects.get(number=2)
        # پزشک فراخوان می‌کند؛ اولویت‌دار اول
        self.client.force_login(self.user)
        self.client.post(reverse("ehr:queue_action"), {"action": "next"})
        special.refresh_from_db()
        self.assertEqual(special.status, "called")
        data = self.client.get(reverse("ehr:queue_display_data")).json()
        self.assertEqual(data["calls"][0]["ticket"], "M02")
        self.assertEqual((data["calls"][0]["doctor"], data["calls"][0]["room"]), (self.doc.full_name, "اتاق ۳"))
        self.assertEqual(data["waiting"][0]["count"], 1)
        body = str(data)
        self.assertNotIn("مریم", body)
        self.assertNotIn(self.appt.national_code, body)
        self.client.post(reverse("ehr:queue_action"), {"action": "next"})
        ticket.refresh_from_db()
        special.refresh_from_db()
        self.assertEqual((ticket.status, special.status), ("called", "done"))
        page = self.client.get(reverse("ehr:my_patients"))
        self.assertContains(page, "M01")
        self.client.post(reverse("ehr:queue_action"), {"action": "skip", "ticket": ticket.pk})
        self.client.post(reverse("ehr:queue_action"), {"action": "call", "ticket": ticket.pk})
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, "called")
        # مانیتور بدون ورود باز می‌شود
        self.client.logout()
        self.assertEqual(self.client.get(reverse("ehr:queue_display")).status_code, 200)
        self.assertEqual(self.client.get(reverse("ehr:queue_display_data") + "?lab=*").status_code, 200)

    def test_doctor_cannot_drive_other_queue_and_walk_in_lookup(self):
        other_user = staff("doc2", "پزشک")
        other = doctor(other_user, slug="dr-b", council="22222")
        self.client.force_login(other_user)
        self.assertEqual(self.client.post(reverse("ehr:queue_action"), {"action": "next", "doctor": self.doc.pk}).status_code, 403)
        with self.assertRaises(ValidationError):
            engagement.walk_in(other, self.reception, "9999999999")
        t = engagement.walk_in(other, self.reception, self.appt.patient.file_number)
        self.assertEqual((t.patient, t.label), (self.appt.patient, "1"))

    def test_lab_calls_merge_on_shared_monitor(self):
        from apps.lab.models import QueueCounter, QueueService
        from apps.lab.services import queue as lab_queue

        service = QueueService.objects.create(name="پذیرش", code="A")
        counter = QueueCounter.objects.create(name="باجه ۱")
        counter.services.add(service)
        lab_queue.issue_ticket(service)
        lab_queue.call_next(counter, self.reception)
        data = engagement.display_data(lab_codes=["*"])
        self.assertEqual((data["calls"][0]["doctor"], data["calls"][0]["room"]), ("آزمایشگاه", "باجه ۱"))
        self.assertEqual(engagement.display_data()["calls"], [])

import datetime

from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.appointments.models import Appointment
from apps.clinic.tests.factories import make_doctor

VALID_NATIONAL_CODE = "0499370899"


class BookingTests(TestCase):
    def setUp(self):
        self.doctor = make_doctor()
        self.day = timezone.localdate() + datetime.timedelta(days=1)
        self.url = reverse("appointments:book", args=[self.doctor.slug])

    def payload(self, **overrides):
        data = {
            "date": self.day.isoformat(),
            "time": "09:00",
            "patient_full_name": "مریم رضایی",
            "national_code": VALID_NATIONAL_CODE,
            "mobile": "09121234567",
            "consent": "on",
        }
        data.update(overrides)
        return data

    def test_booking_page_lists_slots(self):
        response = self.client.get(self.url, {"date": self.day.isoformat()})
        self.assertContains(response, 'value="09:00"')
        self.assertContains(response, 'value="09:30"')

    def test_successful_booking(self):
        response = self.client.post(self.url, self.payload(), follow=True)
        appointment = Appointment.objects.get()
        self.assertEqual(appointment.status, Appointment.Status.PENDING)
        self.assertEqual(len(appointment.tracking_code), 8)
        self.assertIsNotNone(appointment.consent_at)
        self.assertContains(response, appointment.masked_national_code.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")))
        self.assertNotContains(response, VALID_NATIONAL_CODE)

    def test_booked_slot_disappears(self):
        self.client.post(self.url, self.payload())
        self.assertEqual(self.doctor.available_slots(self.day), [datetime.time(9, 30)])

    def test_double_booking_rejected(self):
        self.client.post(self.url, self.payload())
        response = self.client.post(self.url, self.payload(mobile="09351234567"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Appointment.objects.count(), 1)

    def test_db_constraint_blocks_duplicate_active_slot(self):
        common = dict(doctor=self.doctor, date=self.day, time=datetime.time(9), patient_full_name="x",
                      national_code=VALID_NATIONAL_CODE, mobile="09121234567", consent_at=timezone.now())
        Appointment.objects.create(**common)
        with self.assertRaises(IntegrityError):
            Appointment.objects.create(**common)

    def test_cancelled_slot_can_be_rebooked(self):
        self.client.post(self.url, self.payload())
        Appointment.objects.update(status=Appointment.Status.CANCELLED)
        self.client.post(self.url, self.payload())
        self.assertEqual(Appointment.objects.active().count(), 1)

    def test_requires_consent(self):
        data = self.payload()
        del data["consent"]
        self.client.post(self.url, data)
        self.assertFalse(Appointment.objects.exists())

    def test_rejects_invalid_national_code_and_mobile(self):
        self.client.post(self.url, self.payload(national_code="1234567890"))
        self.client.post(self.url, self.payload(mobile="0912"))
        self.assertFalse(Appointment.objects.exists())

    def test_accepts_persian_digits(self):
        self.client.post(self.url, self.payload(national_code="۰۴۹۹۳۷۰۸۹۹", mobile="۰۹۱۲۱۲۳۴۵۶۷"))
        self.assertEqual(Appointment.objects.get().mobile, "09121234567")

    def test_rejects_time_outside_schedule(self):
        self.client.post(self.url, self.payload(time="15:00"))
        self.assertFalse(Appointment.objects.exists())

    def test_offline_doctor_cannot_be_booked(self):
        self.doctor.accepts_online_booking = False
        self.doctor.save()
        self.assertEqual(self.client.get(self.url).status_code, 404)


class TrackingAndPrivacyTests(TestCase):
    def setUp(self):
        self.doctor = make_doctor()
        self.appointment = Appointment.objects.create(
            doctor=self.doctor, date=timezone.localdate() + datetime.timedelta(days=2), time=datetime.time(9),
            patient_full_name="مریم رضایی", national_code=VALID_NATIONAL_CODE, mobile="09121234567",
            consent_at=timezone.now(),
        )
        self.detail_url = reverse("appointments:detail", args=[self.appointment.pk])

    def test_detail_requires_session_ownership(self):
        response = self.client.get(self.detail_url)
        self.assertRedirects(response, reverse("appointments:track"))

    def test_track_with_wrong_mobile_fails(self):
        response = self.client.post(
            reverse("appointments:track"), {"tracking_code": self.appointment.tracking_code, "mobile": "09350000000"}
        )
        self.assertContains(response, "نوبتی با این مشخصات یافت نشد")

    def test_track_then_cancel(self):
        response = self.client.post(
            reverse("appointments:track"), {"tracking_code": self.appointment.tracking_code, "mobile": "09121234567"}
        )
        self.assertRedirects(response, self.detail_url)
        self.client.post(reverse("appointments:cancel", args=[self.appointment.pk]))
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.status, Appointment.Status.CANCELLED)

    def test_cancel_without_session_is_ignored(self):
        self.client.post(reverse("appointments:cancel", args=[self.appointment.pk]))
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.status, Appointment.Status.PENDING)

    def test_past_appointment_cannot_be_cancelled(self):
        self.appointment.date = timezone.localdate() - datetime.timedelta(days=1)
        self.appointment.save()
        self.assertFalse(self.appointment.can_cancel)

    def test_admin_requires_login(self):
        response = self.client.get(reverse("admin:appointments_appointment_changelist"))
        self.assertEqual(response.status_code, 302)

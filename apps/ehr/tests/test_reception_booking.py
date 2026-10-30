"""نوبت حضوری منشی، پرونده واحد با کد ملی در همه بخش‌ها و پیگیری نوبت با کد پیگیری + موبایل یا کد ملی."""

import datetime

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.appointments.models import Appointment
from apps.clinic.models import WorkingHours
from apps.core.templatetags.persian import fa_digits
from apps.ehr import engagement
from apps.ehr.models import ClinicTicket, Patient
from apps.lab.models import LabPatient
from apps.lab.tests.fixtures import NATIONAL_CODE, staff

from .test_ehr import OTHER_CODE, appointment, doctor


def next_weekday_date(days=1):
    return timezone.localdate() + datetime.timedelta(days=days)


class ReceptionBookingTests(TestCase):
    def setUp(self):
        self.secretary = staff("sec", "منشی پزشک")
        self.client.force_login(self.secretary)
        self.doc = doctor()
        self.doc.accepts_online_booking = False  # نوبت اینترنتی بسته؛ منشی باز هم نوبت می‌دهد
        self.doc.save()
        for weekday in range(7):
            WorkingHours.objects.create(doctor=self.doc, weekday=weekday, start_time=datetime.time(9, 0),
                                        end_time=datetime.time(12, 0), slot_minutes=30)
        self.day = next_weekday_date()
        self.url = reverse("ehr:reception_book") + f"?doctor={self.doc.pk}&date={self.day.isoformat()}"

    def post(self, url=None, **extra):
        data = {"national_code": NATIONAL_CODE, "first_name": "مریم", "last_name": "رضایی", "mobile": "۰۹۱۲۱۲۳۴۵۶۷",
                "time": "09:30", "source": "in_person", "consent": "on", "identity_checked": "on"}
        data.update(extra)
        return self.client.post(url or self.url, data)

    def test_page_lists_free_slots_even_when_online_booking_closed(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ثبت نوبت")
        self.assertEqual(len(response.context["form"].slots), 6)
        self.assertEqual(self.doc.available_slots(self.day), [])  # سمت بیمار بسته است

    def test_booking_creates_verified_record_and_confirmed_in_person_appointment(self):
        response = self.post()
        appt = Appointment.objects.get()
        self.assertRedirects(response, reverse("ehr:reception_booked", args=[appt.pk]))
        self.assertEqual((appt.status, appt.source, appt.created_by), ("confirmed", "in_person", self.secretary))
        self.assertEqual(appt.time, datetime.time(9, 30))
        self.assertEqual(appt.mobile, "09121234567")
        patient = Patient.objects.get()
        self.assertEqual(appt.patient, patient)
        self.assertTrue(patient.is_verified)
        self.assertEqual((patient.first_name, patient.last_name), ("مریم", "رضایی"))
        self.assertContains(self.client.get(reverse("ehr:reception_booked", args=[appt.pk])), fa_digits(appt.tracking_code))
        # ساعت گرفته‌شده دیگر آزاد نیست
        self.assertNotIn(datetime.time(9, 30), self.doc.available_slots(self.day, staff=True))

    def test_existing_record_is_reused_and_prefilled(self):
        existing = Patient.objects.create(national_code=NATIONAL_CODE, first_name="مریم", last_name="رضایی",
                                          mobile="09121234567", father_name="حسن", is_verified=True)
        response = self.client.get(self.url + f"&nc={NATIONAL_CODE}")
        self.assertContains(response, "پرونده موجود")
        self.assertEqual(response.context["form"].initial["first_name"], "مریم")
        self.post(first_name="مریم", last_name="رضایی‌نیا", mobile="09350000000")
        self.assertEqual(Patient.objects.count(), 1)
        existing.refresh_from_db()
        # اطلاعات ثبت‌شده بخش دیگر بازنویسی نمی‌شود
        self.assertEqual((existing.last_name, existing.mobile, existing.father_name), ("رضایی", "09121234567", "حسن"))
        self.assertEqual(Appointment.objects.get().patient, existing)

    def test_unverified_record_from_pharmacy_gets_filled(self):
        bare = Patient.objects.create(national_code=NATIONAL_CODE)  # از فروش داروخانه، بدون نام
        self.post()
        bare.refresh_from_db()
        self.assertEqual((bare.first_name, bare.mobile, bare.is_verified), ("مریم", "09121234567", True))

    def test_taken_slot_and_custom_time(self):
        self.post()
        response = self.post(national_code=OTHER_CODE)  # همان ساعت ۹:۳۰ دیگر در فهرست نیست
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Appointment.objects.count(), 1)
        response = self.post(national_code=OTHER_CODE, time="", custom_time="09:30")
        self.assertContains(response, "قبلاً نوبت دارد")
        self.post(national_code=OTHER_CODE, time="", custom_time="۱۸:۴۵")
        self.assertTrue(Appointment.objects.filter(time=datetime.time(18, 45), national_code=OTHER_CODE).exists())

    def test_invalid_national_code_and_missing_consent_rejected(self):
        self.assertContains(self.post(national_code="1234567890"), "error")
        self.assertContains(self.post(consent=""), "error")
        self.assertEqual(Appointment.objects.count(), 0)

    def test_today_with_check_in_issues_queue_ticket(self):
        today = timezone.localdate()
        url = reverse("ehr:reception_book") + f"?doctor={self.doc.pk}&date={today.isoformat()}"
        response = self.post(url, time="", custom_time="23:59", check_in_now="on")
        ticket = ClinicTicket.objects.get()
        self.assertRedirects(response, reverse("ehr:queue_ticket", args=[ticket.pk]), fetch_redirect_response=False)
        self.assertEqual(ticket.appointment.source, "in_person")
        self.assertEqual(ticket.patient.national_code, NATIONAL_CODE)

    def test_requires_appointment_permission(self):
        self.client.force_login(staff("cashier", "صندوقدار داروخانه"))
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_reception_queue_links_to_booking(self):
        self.assertContains(self.client.get(reverse("ehr:queue_reception")), reverse("ehr:reception_book"))


class WalkInNewPatientTests(TestCase):
    def setUp(self):
        self.user = staff("rec", "پذیرش درمانگاه")
        self.doc = doctor()

    def test_walk_in_with_new_national_code_creates_record(self):
        ticket = engagement.walk_in(self.doc, self.user, NATIONAL_CODE, first_name="مریم", last_name="رضایی")
        self.assertEqual(ticket.patient.national_code, NATIONAL_CODE)
        # بخش دیگر (آزمایشگاه) همان پرونده را می‌گیرد
        lab = LabPatient.objects.create(national_code=NATIONAL_CODE, first_name="مریم", last_name="رضایی", sex="f",
                                        birth_date=datetime.date(1985, 3, 1), mobile="09121234567")
        self.assertEqual(lab.patient, ticket.patient)
        self.assertEqual(Patient.objects.count(), 1)

    def test_walk_in_unknown_code_without_name_still_rejected(self):
        with self.assertRaises(Exception):
            engagement.walk_in(self.doc, self.user, NATIONAL_CODE)


class LabReceptionReusesRecordTests(TestCase):
    def setUp(self):
        self.client.force_login(staff("labrec", "پذیرش آزمایشگاه"))
        self.record = Patient.objects.create(national_code=NATIONAL_CODE, first_name="مریم", last_name="رضایی",
                                             mobile="09121234567", sex="f", birth_date=datetime.date(1985, 3, 1))

    def test_search_shows_clinic_record_and_prefills_form(self):
        response = self.client.get(reverse("lab:staff_reception") + f"?q={NATIONAL_CODE}")
        self.assertContains(response, f"?ehr={self.record.pk}")
        response = self.client.get(reverse("lab:staff_reception") + f"?ehr={self.record.pk}")
        initial = response.context["patient_form"].initial
        self.assertEqual((initial["national_code"], initial["first_name"], initial["mobile"]),
                         (NATIONAL_CODE, "مریم", "09121234567"))
        self.assertEqual(initial["birth_date"], "1363/12/10")


@override_settings(TRACKING_LIMIT_PER_HOUR=50)
class TrackingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.appt = appointment(doctor(), days=2)

    def track(self, identifier):
        return self.client.post(reverse("appointments:track"), {"identifier": identifier})

    def test_track_with_one_identifier(self):
        detail = reverse("appointments:detail", args=[self.appt.pk])
        for identifier in (self.appt.tracking_code, "09121234567", "۰۴۹۹۳۷۰۸۹۹"):
            self.client.session.flush()
            self.assertRedirects(self.track(identifier), detail)
        self.assertContains(self.track("00000000"), "یافت نشد")
        self.assertContains(self.track("09350000000"), "یافت نشد")

    def test_parent_sees_all_children_by_shared_mobile(self):
        child = appointment(doctor(slug="dr-b", council="22222"), national_code=OTHER_CODE, name="علی رضایی", days=3)
        response = self.track("09121234567")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "مریم رضایی")
        self.assertContains(response, "علی رضایی")
        self.assertContains(response, reverse("appointments:detail", args=[child.pk]))

    def test_cancel_only_with_tracking_code(self):
        self.track("09121234567")
        cancel = reverse("appointments:cancel", args=[self.appt.pk])
        self.assertNotContains(self.client.get(reverse("appointments:detail", args=[self.appt.pk])), cancel)
        self.client.post(cancel)
        self.appt.refresh_from_db()
        self.assertNotEqual(self.appt.status, "cancelled")
        self.track(self.appt.tracking_code)
        self.client.post(cancel)
        self.appt.refresh_from_db()
        self.assertEqual(self.appt.status, "cancelled")

    @override_settings(TRACKING_LIMIT_PER_HOUR=3)
    def test_rate_limited(self):
        for _ in range(3):
            self.track("09350000000")
        response = self.track("09121234567")
        self.assertContains(response, "تعداد تلاش‌ها زیاد است")

    def test_tracking_code_is_random_not_sequential(self):
        codes = {appointment(doctor(slug=f"d{i}", council=f"2{i}"), national_code=OTHER_CODE).tracking_code for i in range(5)}
        self.assertEqual(len(codes), 5)
        self.assertTrue(all(len(c) == 8 and c.isdigit() for c in codes))

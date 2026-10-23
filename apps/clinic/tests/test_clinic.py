import datetime
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.clinic.models import Department, Service, WorkingHours
from apps.shop.models import Product

from .factories import make_department, make_doctor, make_specialty


class WorkingHoursTests(TestCase):
    def test_slot_times(self):
        doctor = make_doctor(weekdays=[0], start=datetime.time(9), end=datetime.time(10, 10), slot=20)
        wh = WorkingHours.objects.get(doctor=doctor)
        self.assertEqual(
            wh.slot_times(), [datetime.time(9), datetime.time(9, 20), datetime.time(9, 40)]
        )


class AvailabilityTests(TestCase):
    def setUp(self):
        self.doctor = make_doctor()
        self.tomorrow = timezone.localdate() + datetime.timedelta(days=1)

    def test_slots_for_future_day(self):
        self.assertEqual(self.doctor.available_slots(self.tomorrow), [datetime.time(9), datetime.time(9, 30)])

    def test_no_slots_in_past_or_too_far(self):
        self.assertEqual(self.doctor.available_slots(timezone.localdate() - datetime.timedelta(days=1)), [])
        self.assertEqual(self.doctor.available_slots(timezone.localdate() + datetime.timedelta(days=365)), [])

    def test_no_slots_for_inactive_or_offline_doctor(self):
        self.doctor.accepts_online_booking = False
        self.assertEqual(self.doctor.available_slots(self.tomorrow), [])

    def test_today_excludes_past_times(self):
        today = timezone.localdate()
        fake_now = timezone.make_aware(datetime.datetime.combine(today, datetime.time(9, 15)))
        with mock.patch("django.utils.timezone.now", return_value=fake_now):
            self.assertEqual(self.doctor.available_slots(today), [datetime.time(9, 30)])

    def test_available_dates_follow_schedule(self):
        doctor = make_doctor(department=self.doctor.department, slug="dr-sat", weekdays=[5], medical_council_number="MC-2")
        dates = doctor.available_dates(days=14)
        self.assertTrue(dates)
        self.assertTrue(all(d.weekday() == 5 for d in dates))


class ClinicPageTests(TestCase):
    def setUp(self):
        self.dept = make_department(slug="specialists", title="پزشکان متخصص")
        Service.objects.create(department=self.dept, title="اکوکاردیوگرافی", price=10_000_000)
        self.specialty = make_specialty()
        self.doctor = make_doctor(department=self.dept, specialty=self.specialty)

    def test_department_detail(self):
        response = self.client.get(reverse("clinic:department_detail", args=["specialists"]))
        self.assertContains(response, "اکوکاردیوگرافی")
        self.assertContains(response, "۱٬۰۰۰٬۰۰۰ تومان")
        self.assertContains(response, self.doctor.full_name)

    def test_inactive_department_404(self):
        Department.objects.filter(pk=self.dept.pk).update(is_active=False)
        response = self.client.get(reverse("clinic:department_detail", args=["specialists"]))
        self.assertEqual(response.status_code, 404)

    def test_department_list(self):
        response = self.client.get(reverse("clinic:department_list"))
        self.assertContains(response, "پزشکان متخصص")
        self.assertNotContains(response, "طبقه")

    def test_pharmacy_department_shows_products(self):
        make_department(slug="pharmacy", kind=Department.Kind.PHARMACY, title="داروخانه")
        Product.objects.create(kind=Product.Kind.DRUG, name="داروی آزمایشی", slug="test-drug", price=1000, stock=3)
        response = self.client.get(reverse("clinic:department_detail", args=["pharmacy"]))
        self.assertContains(response, "داروی آزمایشی")

    def test_doctor_list_and_filter(self):
        other = make_doctor(department=self.dept, slug="dr-other", first_name="رضا", medical_council_number="MC-9")
        response = self.client.get(reverse("clinic:doctor_list"), {"specialty": "cardio"})
        self.assertContains(response, self.doctor.full_name)
        self.assertNotContains(response, other.full_name)

    def test_inactive_doctor_hidden(self):
        self.doctor.is_active = False
        self.doctor.save()
        self.assertEqual(self.client.get(self.doctor.get_absolute_url()).status_code, 404)
        self.assertNotContains(self.client.get(reverse("clinic:doctor_list")), self.doctor.full_name)

    def test_doctor_detail_shows_schedule(self):
        response = self.client.get(self.doctor.get_absolute_url())
        self.assertContains(response, "شنبه")
        self.assertContains(response, "۰۹:۰۰")

    def test_search(self):
        response = self.client.get(reverse("clinic:search"), {"q": "قلب"})
        self.assertContains(response, self.doctor.full_name)
        response = self.client.get(reverse("clinic:search"), {"q": "اکو"})
        self.assertContains(response, "اکوکاردیوگرافی")
        Product.objects.create(kind=Product.Kind.DRUG, name="ژلوفن", generic_name="Ibuprofen", slug="gelofen", stock=1)
        response = self.client.get(reverse("clinic:search"), {"q": "ibuprofen"})
        self.assertContains(response, "ژلوفن")


class SeedCommandTests(TestCase):
    def test_seed_is_idempotent(self):
        call_command("seed_services", "--demo", stdout=mock.MagicMock())
        call_command("seed_services", "--demo", stdout=mock.MagicMock())
        self.assertEqual(Department.objects.count(), 7)
        self.assertTrue(Department.objects.get(slug="rehab").is_coming_soon)
        self.assertEqual(Product.objects.count(), 5)
        response = self.client.get(reverse("core:home"))
        self.assertContains(response, "کافی‌شاپ ۷۲۴")

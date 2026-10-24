import datetime

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounting import services as acc
from apps.core import sms
from apps.lab.models import LabOrder, LabPatient, SamplingAppointment, SamplingSchedule, Specimen
from apps.lab.services import booking, pricing
from apps.lab.services import orders as order_service

from .fixtures import NATIONAL_CODE, make_order, patient, plans, seed, staff, tests


class PricingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed()

    def test_package_price_is_distributed_without_insurance(self):
        from apps.lab.models import LabPanel

        lines = pricing.price_lines(pricing.expand_selection(panels=[LabPanel.objects.get(code="DIAB")]))
        prices = {line.test.code: line.price for line in lines}
        self.assertEqual(sum(prices.values()), 3_200_000)
        self.assertEqual(prices["FBS"], 502_857)
        self.assertEqual(prices["HBA1C"], 2_194_285)
        self.assertTrue(all(line.patient_share == line.price for line in lines))

    def test_single_test_overrides_panel_and_partial_panel_is_prorated(self):
        from apps.lab.models import LabPanel

        selection = pricing.expand_selection(tests(*["FBS"]), [LabPanel.objects.get(code="DIAB")])
        lines = {line.test.code: line for line in pricing.price_lines(selection)}
        self.assertIsNone(lines["FBS"].panel)
        self.assertEqual(lines["FBS"].price, 550_000)
        # بسته ۳٫۲ میلیون برای ۳٫۵ میلیون تعرفه؛ دو آزمایش باقی‌مانده = ۲٫۹۵ میلیون ← ۲٬۶۹۷٬۱۴۲
        self.assertEqual(lines["HBA1C"].price + lines["CR"].price, 3_200_000 * 2_950_000 // 3_500_000)

    def test_basic_and_supplementary_shares(self):
        basic, supp = plans()
        lines = {l.test.code: l for l in pricing.price_lines(pricing.expand_selection(tests("FBS", "HBA1C")), basic, supp)}
        fbs, a1c = lines["FBS"], lines["HBA1C"]
        self.assertEqual((fbs.price, fbs.basic_share, fbs.supplementary_share, fbs.patient_share), (330_000, 231_000, 79_200, 19_800))
        self.assertEqual((a1c.price, a1c.basic_share, a1c.supplementary_share, a1c.patient_share), (1_440_000, 1_008_000, 345_600, 86_400))

    def test_supplementary_cap_and_rules(self):
        from apps.lab.models import PlanTestRule

        basic, supp = plans(cap=300_000)
        PlanTestRule.objects.create(plan=basic, test=tests("HBA1C")[0], price=2_000_000, coverage_percent=50)
        lines = {l.test.code: l for l in pricing.price_lines(pricing.expand_selection(tests("FBS", "HBA1C")), basic, supp)}
        self.assertEqual(lines["HBA1C"].price, 2_000_000)
        self.assertEqual(lines["HBA1C"].basic_share, 1_000_000)
        self.assertEqual(sum(l.supplementary_share for l in lines.values()), 300_000)
        for line in lines.values():
            self.assertEqual(line.price, line.basic_share + line.supplementary_share + line.patient_share)

    def test_uncovered_test(self):
        from apps.lab.models import PlanTestRule

        basic, _ = plans()
        PlanTestRule.objects.create(plan=basic, test=tests("VITD")[0], is_covered=False)
        line = pricing.price_lines(pricing.expand_selection(tests("VITD")), basic)[0]
        self.assertFalse(line.basic_covered)
        self.assertEqual(line.basic_share, 0)
        self.assertEqual(line.patient_share, line.price)


class OrderPostingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed()

    def setUp(self):
        self.user = staff("rec", "پذیرش آزمایشگاه")
        self.basic, self.supp = plans()
        self.patient = patient()

    def _order(self, **fields):
        defaults = {
            "basic_plan": self.basic, "basic_insurance_no": "123", "supplementary_plan": self.supp,
            "supplementary_insurance_no": "987", "discount": 6_200, "discount_reason": "کارمند",
        }
        defaults.update(fields)
        return make_order(self.user, self.patient, ["FBS", "HBA1C"], post=False, **defaults)

    def test_post_creates_number_specimens_results_and_balanced_journal(self):
        order = self._order()
        order.post(self.user)
        order.refresh_from_db()
        self.assertTrue(order.number.startswith("LAB-"))
        self.assertEqual(order.progress, LabOrder.Progress.SAMPLING)
        self.assertEqual((order.gross_amount, order.basic_share, order.supplementary_share, order.patient_share),
                         (1_770_000, 1_239_000, 424_800, 106_200))
        self.assertEqual(order.net_patient, 100_000)
        self.assertEqual(order.specimens.count(), 2)  # سرم و EDTA
        self.assertEqual(len({s.barcode for s in order.specimens.all()}), 2)
        self.assertEqual(order_service.LabResult.objects.filter(item__order=order).count(), 2)
        self.assertEqual(acc.account_balance(order_service.LAB_REVENUE), -1_770_000)
        self.assertEqual(acc.account_balance(order_service.LAB_DISCOUNT), 6_200)
        self.assertEqual(acc.account_balance(acc.AR_INSURERS), 1_239_000 + 424_800)
        self.assertEqual(acc.account_balance(acc.AR_CUSTOMERS), 100_000)
        _, totals = acc.trial_balance()
        self.assertEqual(totals["debit"], totals["credit"])
        self.assertIsNotNone(order.report_code)

    def test_validation_rules(self):
        cases = [
            ({"basic_insurance_no": ""}, "شماره بیمه پایه"),
            ({"discount": 200_000}, "تخفیف بیشتر"),
            ({"discount_reason": ""}, "علت تخفیف"),
        ]
        for fields, message in cases:
            order = self._order(**fields)
            with self.assertRaisesMessage(ValidationError, message):
                order.post(self.user)
        self.basic.end_date = timezone.localdate() - datetime.timedelta(days=1)
        self.basic.save()
        with self.assertRaisesMessage(ValidationError, "معتبر نیست"):
            self._order().post(self.user)

    def test_approval_code_required_by_plan(self):
        self.supp.requires_approval_code = True
        self.supp.save()
        with self.assertRaisesMessage(ValidationError, "کد تأیید بیمه تکمیلی"):
            self._order().post(self.user)
        self._order(supplementary_approval_code="A-1").post(self.user)

    def test_stale_totals_must_be_repriced(self):
        order = self._order()
        order.basic_plan = None
        order.save()
        with self.assertRaisesMessage(ValidationError, "محاسبه مجدد"):
            order.post(self.user)
        order_service.reprice(order)
        order.post(self.user)

    def test_home_sampling_requires_address_and_eligible_tests(self):
        order = make_order(self.user, self.patient, ["HIV"], post=False, sampling_place="home", home_fee=1_500_000)
        with self.assertRaises(ValidationError):
            order.post(self.user)
        self.patient.address = "تهران"
        self.patient.save()
        with self.assertRaisesMessage(ValidationError, "در منزل انجام نمی‌شوند"):
            order.post(self.user)

    def test_payments_refunds_and_reverse(self):
        order = self._order()
        order.post(self.user)
        with self.assertRaisesMessage(ValidationError, "بیشتر از مانده"):
            order_service.receive(order, self.user, 100_001)
        order_service.receive(order, self.user, 60_000)
        order_service.receive(order, self.user, 40_000, method="card", reference_no="778899")
        self.assertEqual(order.balance_due, 0)
        self.assertEqual(acc.account_balance(acc.AR_CUSTOMERS), 0)
        with self.assertRaisesMessage(ValidationError, "مسترد"):
            order.reverse(self.user, "اشتباه پذیرش")
        order_service.receive(order, self.user, 100_000, direction="refund")
        order.reverse(self.user, "اشتباه پذیرش")
        self.assertEqual(acc.account_balance(order_service.LAB_REVENUE), 0)
        self.assertEqual(acc.account_balance(acc.AR_INSURERS), 0)
        _, totals = acc.trial_balance()
        self.assertEqual(totals["debit"], totals["credit"])

    def test_posted_order_items_locked(self):
        order = self._order()
        order.post(self.user)
        with self.assertRaises(ValidationError):
            order_service.set_order_tests(order, tests("TSH"))


class ReceptionViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed()

    def setUp(self):
        self.user = staff("rec", "پذیرش آزمایشگاه")
        self.client.force_login(self.user)

    def _payload(self, **extra):
        from apps.lab.models import LabTest

        data = {
            "p-national_code": "۰۴۹۹۳۷۰۸۹۹", "p-first_name": "علی", "p-last_name": "کریمی", "p-sex": "m",
            "p-birth_date": "۱۳۶۵/۰۴/۲۰", "p-mobile": "09121234567", "o-priority": "routine", "o-sampling_place": "lab",
            "o-delivery": "online", "o-home_fee": "0", "o-tests": [LabTest.objects.get(code="TSH").pk],
        }
        data.update(extra)
        return data

    def test_reception_creates_patient_and_draft_then_post_and_pay(self):
        response = self.client.post(reverse("lab:staff_reception"), self._payload())
        order = LabOrder.objects.get()
        self.assertRedirects(response, reverse("lab:staff_order", args=[order.pk]))
        patient = LabPatient.objects.get()
        self.assertEqual(patient.national_code, NATIONAL_CODE)
        self.assertEqual(patient.birth_date, datetime.date(1986, 7, 11))
        self.assertEqual(order.gross_amount, 1_900_000)
        self.client.post(reverse("lab:staff_order_action", args=[order.pk, "post"]))
        order.refresh_from_db()
        self.assertEqual(order.status, "posted")
        page = self.client.get(reverse("lab:staff_order", args=[order.pk]))
        self.assertContains(page, order.number)
        token = page.context["token"]
        for _ in range(2):  # ارسال دوباره فرم فقط یک دریافت ثبت می‌کند
            self.client.post(reverse("lab:staff_order_action", args=[order.pk, "payment"]),
                             {"direction": "receive", "method": "cash", "amount": "۱۹۰۰۰۰۰", "token": token})
        self.assertEqual(order.payments.count(), 1)
        self.assertEqual(order.balance_due, 0)
        self.assertContains(self.client.get(reverse("lab:staff_receipt", args=[order.pk])), "۱٬۹۰۰٬۰۰۰")
        self.assertContains(self.client.get(reverse("lab:staff_labels", args=[order.pk])), "<svg")

    def test_reception_cannot_give_discount_without_permission(self):
        self.client.post(reverse("lab:staff_reception"), self._payload(**{"o-discount": "500000", "o-discount_reason": "x"}))
        self.assertEqual(LabOrder.objects.get().discount, 0)

    def test_existing_patient_is_updated_not_duplicated(self):
        p = patient(sex="m")
        self.client.post(f"{reverse('lab:staff_reception')}?patient={p.pk}", self._payload(**{"p-mobile": "09350000000"}))
        self.assertEqual(LabPatient.objects.count(), 1)
        p.refresh_from_db()
        self.assertEqual(p.mobile, "09350000000")

    def test_sampler_cannot_open_reception_or_results(self):
        self.client.force_login(staff("smp", "نمونه‌گیر آزمایشگاه"))
        self.assertEqual(self.client.get(reverse("lab:staff_reception")).status_code, 403)
        order = make_order(self.user, patient(), ["TSH"])
        self.assertEqual(self.client.get(reverse("lab:staff_results", args=[order.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("lab:staff_order", args=[order.pk])).status_code, 200)

    def test_anonymous_redirected_to_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("lab:staff_dashboard")).status_code, 302)


class BookingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed()

    def setUp(self):
        sms.outbox.clear()
        self.day = timezone.localdate() + datetime.timedelta(days=1)
        SamplingSchedule.objects.create(
            kind="lab", weekday=self.day.weekday(), start_time=datetime.time(7), end_time=datetime.time(8),
            slot_minutes=30, capacity=1,
        )

    def _book(self, code=NATIONAL_CODE, time="07:00"):
        return self.client.post(
            f"{reverse('lab:book')}?kind=lab&date={self.day.isoformat()}",
            {"kind": "lab", "date": self.day.isoformat(), "time": time, "full_name": "مریم رضایی", "national_code": code,
             "mobile": "۰۹۱۲۱۲۳۴۵۶۷", "consent": "on", "tests": [tests("FBS")[0].pk]},
        )

    def test_book_capacity_and_cancel(self):
        response = self._book()
        appointment = SamplingAppointment.objects.get()
        self.assertRedirects(response, reverse("lab:booking_detail", args=[appointment.pk]))
        self.assertIn(appointment.tracking_code, sms.outbox[-1]["text"])
        self.assertEqual(booking.available_slots("lab", self.day), [(datetime.time(7, 30), 1)])
        with self.assertRaisesMessage(ValidationError, "پر شده"):
            booking.book("lab", self.day, datetime.time(7), full_name="x", national_code="0013542419", mobile="09120000000")
        with self.assertRaisesMessage(ValidationError, "نوبت فعال"):
            booking.book("lab", self.day, datetime.time(7, 30), full_name="x", national_code=NATIONAL_CODE, mobile="09120000000")
        self.client.post(reverse("lab:booking_cancel", args=[appointment.pk]))
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, "cancelled")
        self.assertEqual(len(booking.available_slots("lab", self.day)), 2)

    def test_track_needs_code_and_mobile(self):
        self._book()
        appointment = SamplingAppointment.objects.get()
        self.client.session.flush()
        other = self.client_class()
        self.assertRedirects(other.get(reverse("lab:booking_detail", args=[appointment.pk])), reverse("lab:booking_track"))
        response = other.post(reverse("lab:booking_track"), {"tracking_code": appointment.tracking_code, "mobile": "09121234567"})
        self.assertRedirects(response, reverse("lab:booking_detail", args=[appointment.pk]))

    def test_home_booking_requires_address(self):
        SamplingSchedule.objects.create(kind="home", weekday=self.day.weekday(), start_time=datetime.time(9), end_time=datetime.time(10))
        response = self.client.post(f"{reverse('lab:book')}?kind=home&date={self.day.isoformat()}", {
            "kind": "home", "date": self.day.isoformat(), "time": "09:00", "full_name": "x", "national_code": NATIONAL_CODE,
            "mobile": "09121234567", "consent": "on",
        })
        self.assertContains(response, "آدرس برای نمونه‌گیری در منزل الزامی است")

    def test_reception_from_appointment_marks_arrived(self):
        self._book()
        appointment = SamplingAppointment.objects.get()
        user = staff("rec", "پذیرش آزمایشگاه")
        self.client.force_login(user)
        page = self.client.get(reverse("lab:staff_reception"), {"appointment": appointment.pk})
        self.assertEqual(page.context["patient_form"].initial["national_code"], NATIONAL_CODE)
        order = make_order(user, patient(), ["FBS"], post=False, appointment=appointment)
        order.post(user)
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, "arrived")
        self.assertEqual(Specimen.objects.filter(order=order).count(), 1)

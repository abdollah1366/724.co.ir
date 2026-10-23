import datetime
import json
import re
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounting import services as acc
from apps.core import sms
from apps.lab import barcode
from apps.lab.models import (
    Instrument, LabOrder, LabResult, LabSection, QCMaterial, QCResult, ResultRevision, Specimen,
)
from apps.lab.services import lis
from apps.lab.services import orders as order_service

from .fixtures import NATIONAL_CODE, make_order, patient, plans, seed, staff, tests


def result(order, code):
    return LabResult.objects.get(item__order=order, item__test__code=code)


def collect_all(order, user):
    for specimen in order.specimens.all():
        lis.collect_specimen(specimen, user)


class BarcodeTests(TestCase):
    def test_code128_checksum_and_svg(self):
        self.assertEqual(barcode.encode("1234"), [105, 12, 34, 82, 106])
        self.assertEqual(barcode.encode("AB")[-2], (104 + 33 + 2 * 34) % 103)
        self.assertIn("<rect", barcode.svg("140500001201"))


class WestgardTests(TestCase):
    def test_rules(self):
        self.assertEqual(lis.westgard([Decimal("1.5")]), ("accepted", []))
        self.assertEqual(lis.westgard([Decimal("2.5")]), ("warning", ["1-2s"]))
        self.assertEqual(lis.westgard([Decimal("3.1")])[1], ["1-3s"])
        self.assertIn("2-2s", lis.westgard([Decimal("2.2"), Decimal("2.1")])[1])
        self.assertIn("R-4s", lis.westgard([Decimal("2.2"), Decimal("-2.1")])[1])
        self.assertIn("4-1s", lis.westgard([Decimal("1.2")] * 4)[1])
        self.assertIn("10x", lis.westgard([Decimal("0.3")] * 10)[1])


class ResultWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed()

    def setUp(self):
        sms.outbox.clear()
        self.reception = staff("rec", "پذیرش آزمایشگاه")
        self.tech = staff("tech", "کارشناس آزمایشگاه")
        self.director = staff("dir", "مسئول فنی آزمایشگاه")
        self.patient = patient()
        self.order = make_order(self.reception, self.patient, ["FBS", "HBA1C", "HBSAG"])

    def test_cannot_enter_before_collection(self):
        with self.assertRaisesMessage(ValidationError, "گرفته نشده"):
            lis.enter_result(result(self.order, "FBS"), "95", self.tech)

    def test_flags_reference_and_progress(self):
        collect_all(self.order, self.tech)
        self.order.refresh_from_db()
        self.assertEqual(self.order.progress, LabOrder.Progress.IN_PROGRESS)
        fbs = lis.enter_result(result(self.order, "FBS"), "۱۲۰", self.tech)
        self.assertEqual((fbs.numeric_value, fbs.flag, fbs.is_critical, fbs.reference_text), (Decimal("120"), "H", False, "70 - 99"))
        self.assertEqual(lis.enter_result(result(self.order, "FBS"), "45", self.tech).flag, "LL")
        hbs = lis.enter_result(result(self.order, "HBSAG"), "مثبت", self.tech)
        self.assertEqual(hbs.flag, "A")
        with self.assertRaisesMessage(ValidationError, "گزینه‌ها"):
            lis.enter_result(result(self.order, "HBSAG"), "زیاد", self.tech)
        with self.assertRaisesMessage(ValidationError, "عدد"):
            lis.enter_result(result(self.order, "HBA1C"), "بالا", self.tech)

    def test_critical_requires_notification_before_approval(self):
        collect_all(self.order, self.tech)
        fbs = lis.enter_result(result(self.order, "FBS"), "450", self.tech)
        self.assertTrue(fbs.is_critical)
        self.assertIn(fbs, lis.unnotified_critical())
        with self.assertRaisesMessage(ValidationError, "بحرانی"):
            lis.approve_result(fbs, self.director)
        lis.notify_critical(fbs, self.tech, "doctor", "دکتر احمدی", "09120000000", read_back=True)
        self.assertNotIn(fbs, lis.unnotified_critical())
        self.assertEqual(lis.approve_result(fbs, self.director).status, "approved")

    def test_approved_result_locked_amend_keeps_history(self):
        collect_all(self.order, self.tech)
        a1c = lis.approve_result(lis.enter_result(result(self.order, "HBA1C"), "7.2", self.tech), self.director)
        a1c.value = "5"
        with self.assertRaises(PermissionError):
            a1c.save()
        with self.assertRaises(ValidationError):
            lis.enter_result(a1c, "5.0", self.tech)
        with self.assertRaisesMessage(ValidationError, "علت"):
            lis.amend_result(a1c, "6.9", "", self.director)
        amended = lis.amend_result(a1c, "6.9", "اشتباه در ورود", self.director)
        self.assertEqual((amended.version, amended.status, amended.value), (2, "entered", "6.9"))
        revision = ResultRevision.objects.get()
        self.assertEqual((revision.old_value, revision.old_approved_by), ("7.2", self.director))
        with self.assertRaises(PermissionError):
            revision.delete()

    def test_full_approval_marks_ready_and_sms_has_no_medical_data(self):
        collect_all(self.order, self.tech)
        for code, value in [("FBS", "95"), ("HBA1C", "5.1"), ("HBSAG", "منفی")]:
            lis.approve_result(lis.enter_result(result(self.order, code), value, self.tech), self.director)
        self.order.refresh_from_db()
        self.assertEqual(self.order.progress, LabOrder.Progress.READY)
        text = sms.outbox[-1]["text"]
        self.assertIn(self.order.number, text)
        for word in ("FBS", "قند", "هپاتیت", "95"):
            self.assertNotIn(word, text)
        with self.assertRaisesMessage(ValidationError, "تأییدشده"):
            self.order.reverse(self.director, "x")

    @override_settings(LAB_RULES={**__import__("django.conf").conf.settings.LAB_RULES, "approver_must_differ": True})
    def test_four_eyes_rule(self):
        collect_all(self.order, self.tech)
        entered = lis.enter_result(result(self.order, "FBS"), "95", self.director)
        with self.assertRaisesMessage(ValidationError, "متفاوت"):
            lis.approve_result(entered, self.director)

    def test_reject_specimen_creates_recollection(self):
        serum = self.order.specimens.get(specimen_type__code="SER")
        lis.collect_specimen(serum, self.tech)
        new = lis.reject_specimen(serum, "همولیز", self.tech)
        self.assertEqual(new.recollect_of, serum)
        self.assertNotEqual(new.barcode, serum.barcode)
        self.assertEqual(result(self.order, "FBS").specimen, new)
        with self.assertRaises(ValidationError):
            lis.enter_result(result(self.order, "FBS"), "95", self.tech)

    def test_delta_check(self):
        collect_all(self.order, self.tech)
        lis.approve_result(lis.enter_result(result(self.order, "HBA1C"), "6.0", self.tech), self.director)
        second = make_order(self.reception, self.patient, ["HBA1C"])
        collect_all(second, self.tech)
        entered = lis.enter_result(result(second, "HBA1C"), "8.0", self.tech)
        self.assertIn("33٪", entered.delta_alert)

    def test_qc_record_and_enforcement(self):
        material = QCMaterial.objects.create(
            test=tests("FBS")[0], name="کنترل نرمال", lot="L1", mean=Decimal("100"), sd=Decimal("4"),
            expiry=timezone.localdate() + datetime.timedelta(days=30),
        )
        collect_all(self.order, self.tech)
        entered = lis.enter_result(result(self.order, "FBS"), "95", self.tech)
        self.assertEqual(lis.qc_status(material.test), "missing")
        rules = {**__import__("django.conf").conf.settings.LAB_RULES, "require_qc_for_approval": True}
        with override_settings(LAB_RULES=rules):
            with self.assertRaisesMessage(ValidationError, "کنترل کیفی"):
                lis.approve_result(entered, self.director)
            self.assertEqual(lis.record_qc(material, "۱۰۳", self.tech).status, QCResult.Status.ACCEPTED)
            lis.approve_result(entered, self.director)
        self.assertEqual(lis.record_qc(material, "114", self.tech).violations, "1-3s")
        self.assertEqual(lis.qc_status(material.test), "rejected")

    def test_instrument_api(self):
        instrument = Instrument.objects.create(name="Mindray BS-240", code="BS240", section=LabSection.objects.get(code="BIO"))
        key = instrument.set_api_key()
        serum = self.order.specimens.get(specimen_type__code="SER")
        lis.collect_specimen(serum, self.tech)
        url = reverse("lab:api_instrument_results")
        body = json.dumps({"barcode": serum.barcode, "results": [{"code": "FBS", "value": "101"}, {"code": "TSH", "value": "1"}]})
        self.assertEqual(self.client.post(url, body, content_type="application/json", HTTP_X_INSTRUMENT_KEY="bad").status_code, 401)
        response = self.client.post(url, body, content_type="application/json", HTTP_X_INSTRUMENT_KEY=key)
        self.assertEqual(response.json()["saved"], ["FBS"])
        self.assertEqual(len(response.json()["errors"]), 1)
        fbs = result(self.order, "FBS")
        self.assertEqual((fbs.status, fbs.source, fbs.instrument), ("entered", "instrument", instrument))

    def test_results_page_enter_and_approve(self):
        collect_all(self.order, self.tech)
        self.client.force_login(self.tech)
        url = reverse("lab:staff_results", args=[self.order.pk])
        fbs = result(self.order, "FBS")
        self.client.post(url, {"action": "save", f"value_{fbs.pk}": "98"})
        fbs.refresh_from_db()
        self.assertEqual(fbs.status, "entered")
        self.client.post(url, {"action": "approve", f"approve_{fbs.pk}": "1"})  # کارشناس مجوز تأیید ندارد
        fbs.refresh_from_db()
        self.assertEqual(fbs.status, "entered")
        self.client.force_login(self.director)
        self.client.post(url, {"action": "approve", f"approve_{fbs.pk}": "1"})
        fbs.refresh_from_db()
        self.assertEqual(fbs.status, "approved")
        report = self.client.get(reverse("lab:staff_report", args=[self.order.pk]))
        self.assertContains(report, "قند خون ناشتا")
        self.assertContains(report, "هنوز آماده نشده")
        self.assertContains(self.client.get(reverse("lab:staff_worklist")), self.order.number)


class InsuranceClaimTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed()

    def test_claim_build_post_settle_and_csv(self):
        user = staff("boss", "مدیر آزمایشگاه")
        basic, supp = plans()
        p = patient()
        fields = {"basic_plan": basic, "basic_insurance_no": "123", "supplementary_plan": supp, "supplementary_insurance_no": "9"}
        o1 = make_order(user, p, ["FBS", "HBA1C"], **fields)
        o2 = make_order(user, p, ["TSH"], **fields)
        make_order(user, p, ["CR"])  # بدون بیمه
        today = timezone.localdate()
        claim = order_service.build_claim(basic, today, today, user)
        self.assertEqual({l.order for l in claim.lines.all()}, {o1, o2})
        self.assertEqual(claim.total, o1.basic_share + o2.basic_share)
        # پذیرش‌ها دوبار در صورت‌حساب همان بیمه نمی‌آیند
        self.assertFalse(order_service.build_claim(basic, today, today, user).lines.exists())
        claim.post(user)
        with self.assertRaisesMessage(ValidationError, "صورت‌حساب"):
            o1.reverse(user, "x")
        self.client.force_login(user)
        csv_text = self.client.get(reverse("lab:staff_claim", args=[claim.pk]), {"format": "csv"}).content.decode("utf-8-sig")
        self.assertIn(o1.number, csv_text)
        self.assertIn(NATIONAL_CODE, csv_text)
        total = claim.total
        self.client.post(reverse("lab:staff_claim", args=[claim.pk]), {
            "action": "settle", "received": str(total - 50_000), "deductions": "50000", "method": "bank", "reference_no": "TR-1",
        })
        self.assertEqual(claim.settled_total, total)
        insurer_balance = [r for r in acc.party_balances() if r["party_id"] == basic.insurer_id]
        self.assertEqual(insurer_balance, [])
        self.assertEqual(acc.account_balance(order_service.INSURANCE_DEDUCTIONS), 50_000)
        _, totals = acc.trial_balance()
        self.assertEqual(totals["debit"], totals["credit"])
        self.assertContains(self.client.get(reverse("lab:staff_dashboard")), "کل طلب از بیمه‌ها")


class StaffPagesSmokeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed()

    def test_pages_render_for_superuser(self):
        user = staff("root", superuser=True)
        order = make_order(user, patient(), ["FBS", "WBC"])
        material = QCMaterial.objects.create(test=tests("FBS")[0], name="c", lot="1", mean=100, sd=3,
                                             expiry=timezone.localdate() + datetime.timedelta(days=9))
        self.client.force_login(user)
        for name, args in [
            ("lab:staff_dashboard", []), ("lab:staff_reception", []), ("lab:staff_orders", []),
            ("lab:staff_order", [order.pk]), ("lab:staff_results", [order.pk]), ("lab:staff_worklist", []),
            ("lab:staff_qc", []), ("lab:staff_qc_chart", [material.pk]), ("lab:staff_claims", []),
            ("lab:staff_cashier", []), ("lab:staff_appointments", []), ("lab:staff_report", [order.pk]),
        ]:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200)
        self.assertEqual(self.client.get("/admin/lab/").status_code, 200)
        self.assertTrue(re.search(r"\d{11}", Specimen.objects.filter(order=order).first().barcode))

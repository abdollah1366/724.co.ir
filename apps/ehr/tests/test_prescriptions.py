from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.audit.documents import DocStatus
from apps.ehr import pharmacy, services
from apps.ehr.models import Allergy, GenericDrug, Prescription
from apps.inventory.tests.fixtures import device, drug, open_session, receive
from apps.lab.tests.fixtures import staff
from apps.sales.models import SalesInvoice, SalesPayment, SalesReturn, SalesReturnLine

from .test_ehr import appointment, doctor


class PrescriptionTests(TestCase):
    def setUp(self):
        self.doctor_user = staff("doc", "پزشک")
        self.pharmacist = staff("pharm", "داروساز مسئول فنی")
        self.doc = doctor(self.doctor_user)
        self.patient = appointment(self.doc).patient
        self.metformin = GenericDrug.objects.create(name="Metformin", name_fa="متفورمین", dosage_form="قرص", strength="500 mg",
                                                    atc_code="A10BA02")
        self.amox = GenericDrug.objects.create(name="Amoxicillin", dosage_form="کپسول", strength="500 mg", atc_code="J01CA04")
        self.glucophage = drug("glucophage", name="گلوکوفاژ ۵۰۰", price=200_000, generic=self.metformin,
                               requires_prescription=True, pregnancy_note="در بارداری با نظر پزشک")
        self.amoxil = drug("amoxil", name="آموکسیل", price=100_000, generic=self.amox, requires_prescription=True)
        self.glucometer = device("glucometer", name="گلوکومتر", price=5_000_000, track_serial=False)
        receive(self.glucophage, 100, 100_000)
        receive(self.amoxil, 5, 50_000, batch="A1")
        receive(self.glucometer, 2, 3_000_000, batch="G1")
        self.client.force_login(self.doctor_user)

    def new_rx(self):
        self.client.post(reverse("ehr:prescription_new", args=[self.patient.pk]))
        return Prescription.objects.get(status="draft")

    def add(self, rx, value, qty, **extra):
        return self.client.post(reverse("ehr:prescription", args=[rx.pk]), {"action": "add", "drug": value, "quantity": qty, **extra})

    def issue(self, rx):
        self.client.post(reverse("ehr:prescription", args=[rx.pk]), {"action": "issue", "m-note": ""})
        rx.refresh_from_db()
        return rx

    def test_doctor_sees_stock_writes_and_issues(self):
        rx = self.new_rx()
        results = self.client.get(reverse("ehr:drug_search"), {"q": "metf"}).json()["results"]
        self.assertIn("موجودی داروخانه: 100", results[0]["label"])
        self.add(rx, f"G{self.metformin.pk} Metformin", "۶۰", frequency="روزی ۲ بار", duration_days="30")
        self.add(rx, f"P{self.glucometer.pk}", 1)
        self.add(rx, "nonsense", 1)
        self.assertEqual(rx.items.count(), 2)
        page = self.client.get(reverse("ehr:prescription", args=[rx.pk]))
        self.assertContains(page, "گلوکوفاژ ۵۰۰")
        self.assertContains(page, "۱۰۰ عدد")
        rx = self.issue(rx)
        self.assertTrue(rx.number.startswith("RX-"))
        self.assertEqual(rx.dispense_status, "تحویل نشده")
        with self.assertRaises(ValidationError):
            rx.items.first().delete()
        self.assertEqual(self.add(rx, f"G{self.amox.pk}", 1).status_code, 403)
        self.assertContains(self.client.get(reverse("ehr:chart", args=[self.patient.pk])), rx.number)
        self.assertEqual(len(pharmacy.current_medications(self.patient)), 2)
        self.assertEqual(self.client.get(reverse("ehr:prescription_print", args=[rx.pk])).status_code, 200)

    def test_allergy_blocks_until_override_and_duplicates_warn(self):
        Allergy.objects.create(patient=self.patient, substance="amoxicillin", severity="severe", recorded_by=self.doctor_user)
        rx = self.new_rx()
        self.add(rx, f"G{self.amox.pk}", 10)
        self.assertEqual(rx.items.count(), 0)
        self.add(rx, f"G{self.amox.pk}", 10, override="on")
        self.assertEqual(rx.items.count(), 1)
        self.add(rx, f"G{self.metformin.pk}", 30)
        self.issue(rx)
        second = self.new_rx()
        warnings = pharmacy.alerts(self.patient, self.metformin)
        self.assertTrue(any("تکرار دارو" in text for _, text in warnings))
        other = GenericDrug.objects.create(name="Metformin XR", dosage_form="قرص", strength="750 mg", atc_code="A10BA02")
        self.assertTrue(any("هم‌گروه" in text for _, text in pharmacy.alerts(self.patient, other)))
        self.assertTrue(second.is_draft)

    def test_pharmacist_dispenses_partially_through_pos(self):
        rx = self.new_rx()
        self.add(rx, f"G{self.metformin.pk}", 60)
        self.add(rx, f"P{self.glucometer.pk}", 1)
        rx = self.issue(rx)
        met_item, glu_item = list(rx.items.all())
        self.client.force_login(self.pharmacist)
        queue = self.client.get(reverse("ehr:pharmacy_queue"))
        self.assertContains(queue, rx.number)
        url = reverse("ehr:pharmacy_prescription", args=[rx.pk])
        response = self.client.post(url, {"action": "dispense", f"product-{met_item.pk}": self.glucophage.pk, f"qty-{met_item.pk}": 30})
        self.assertContains(response, "شیفت")  # صندوق باز نیست
        session = open_session(self.pharmacist)
        response = self.client.post(url, {"action": "dispense", f"product-{met_item.pk}": self.glucophage.pk,
                                          f"qty-{met_item.pk}": 30, f"product-{glu_item.pk}": self.glucometer.pk, f"qty-{glu_item.pk}": 1})
        self.assertRedirects(response, reverse("backoffice:pos"), fetch_redirect_response=False)
        invoice = SalesInvoice.objects.get(session=session)
        self.assertEqual((invoice.patient, invoice.prescription_number), (self.patient, rx.number))
        self.assertEqual(invoice.prescriber_council_no, self.doc.medical_council_number)
        SalesPayment.objects.create(document=invoice, method=SalesPayment.Method.CASH, amount=invoice.total)
        invoice.post(self.pharmacist)
        self.assertEqual(met_item.dispensed_quantity, 30)
        self.assertEqual(rx.dispense_status, "تحویل ناقص")
        self.glucophage.refresh_from_db()
        self.assertEqual(self.glucophage.stock, 70)
        # بیش از باقی‌مانده تحویل نمی‌شود، حتی از مسیر مستقیم صندوق
        with self.assertRaisesMessage(ValidationError, "نسخه بیشتر است"):
            pharmacy.dispense(rx, self.pharmacist, [(met_item, self.glucophage, 31)])
        ret = SalesReturn.objects.create(invoice=invoice, reason="عارضه", created_by=self.pharmacist, session=session)
        SalesReturnLine.objects.create(document=ret, invoice_line=invoice.lines.get(product=self.glucophage), quantity=10)
        ret.post(self.pharmacist)
        self.assertEqual(met_item.remaining, 40)
        kinds = {e.kind for e in services.timeline(self.patient)}
        self.assertTrue({"prescription", "drug", "equipment"} <= kinds)
        self.client.post(url, {"action": "counsel", "c-topics": "نحوه مصرف متفورمین", "c-notes": "همراه غذا"})
        self.assertEqual(rx.counselings.count(), 1)
        # داروساز به پرونده بالینی دسترسی ندارد
        self.assertEqual(self.client.get(reverse("ehr:chart", args=[self.patient.pk])).status_code, 403)

    def test_wrong_product_or_patient_rejected_at_posting(self):
        rx = self.new_rx()
        self.add(rx, f"G{self.metformin.pk}", 10)
        rx = self.issue(rx)
        item = rx.items.get()
        session = open_session(self.pharmacist)
        invoice = pharmacy.dispense(rx, self.pharmacist, [(item, self.glucophage, 5)])
        line = invoice.lines.get()
        invoice.patient = None
        invoice.save()
        SalesPayment.objects.create(document=invoice, method=SalesPayment.Method.CASH, amount=invoice.total)
        with self.assertRaisesMessage(ValidationError, "بیمار"):
            invoice.post(self.pharmacist)
        with self.assertRaisesMessage(ValidationError, "مجاز نیست"):
            pharmacy.dispense(rx, self.pharmacist, [(item, self.amoxil, 1)])
        self.assertEqual(line.prescription_item, item)
        self.assertEqual(session.status, "open")

    def test_cancel_and_expiry(self):
        rx = self.new_rx()
        self.add(rx, f"G{self.metformin.pk}", 10)
        rx = self.issue(rx)
        self.client.post(reverse("ehr:prescription_cancel", args=[rx.pk]), {"reason": "تغییر درمان"})
        rx.refresh_from_db()
        self.assertEqual(rx.status, "cancelled")
        with self.assertRaises(ValidationError):
            pharmacy.dispense(rx, self.pharmacist, [])

    def test_drug_info_for_counseling(self):
        response = self.client.get(reverse("ehr:drug_info", args=["g", self.metformin.pk]))
        self.assertContains(response, "در بارداری با نظر پزشک")
        self.assertContains(response, "گلوکوفاژ")
        self.assertEqual(DocStatus.POSTED, "posted")

import datetime

from django.apps import apps as global_apps
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.appointments.models import Appointment
from apps.clinic.models import Department, Doctor
from apps.ehr import services
from apps.ehr.models import Patient, RecordAccess
from apps.inventory.tests.fixtures import device, drug, open_session, pos_sale, receive
from apps.lab.models import LabPatient, LabResult
from apps.lab.services import lis
from apps.lab.tests.fixtures import NATIONAL_CODE, make_order, patient as lab_patient, seed, staff
from apps.sales.models import SalesReturn, SalesReturnLine

OTHER_CODE = "0013542419"


def doctor(user=None, slug="dr-a", council="11111"):
    department, _ = Department.objects.get_or_create(slug="clinic", defaults={"title": "درمانگاه", "kind": "clinic", "summary": "-"})
    return Doctor.objects.create(first_name="علی", last_name="محمدی", slug=slug, department=department,
                                 medical_council_number=council, user=user)


def appointment(doc, national_code=NATIONAL_CODE, name="مریم رضایی", days=0, **kw):
    return Appointment.objects.create(
        doctor=doc, date=timezone.localdate() + datetime.timedelta(days=days), time=datetime.time(10, 0),
        patient_full_name=name, national_code=national_code, mobile="09121234567", consent_at=timezone.now(), **kw
    )


class LinkingTests(TestCase):
    def test_lab_patient_creates_verified_record(self):
        lp = lab_patient()
        p = lp.patient
        self.assertTrue(p.is_verified)
        self.assertEqual((p.national_code, p.first_name, p.sex, p.birth_date), (NATIONAL_CODE, "مریم", "f", lp.birth_date))
        self.assertTrue(p.file_number.startswith("P-"))

    def test_online_booking_creates_unverified_record_and_lab_reception_verifies_it(self):
        a = appointment(doctor(), name="سید مریم رضایی")
        p = a.patient
        self.assertFalse(p.is_verified)
        self.assertEqual((p.first_name, p.last_name), ("سید", "مریم رضایی"))
        lp = lab_patient()
        p.refresh_from_db()
        self.assertEqual(lp.patient, p)
        self.assertTrue(p.is_verified)
        self.assertEqual((p.first_name, p.last_name), ("مریم", "رضایی"))
        self.assertEqual(Patient.objects.count(), 1)

    def test_booking_never_overwrites_existing_record(self):
        lp = lab_patient()
        appointment(doctor(), name="نام جعلی")
        lp.patient.refresh_from_db()
        self.assertEqual(lp.patient.full_name, "مریم رضایی")
        self.assertEqual(lp.patient.appointments.count(), 1)

    def test_foreign_patient_linked_by_foreign_id(self):
        lp = lab_patient(national_code=None, foreign_id="P1234567")
        self.assertEqual(lp.patient.foreign_id, "P1234567")
        self.assertIsNone(lp.patient.national_code)

    def test_migration_backfills_existing_records(self):
        from importlib import import_module

        link = import_module("apps.ehr.migrations.0002_link_existing_records").link
        lab_patient()
        appointment(doctor(), national_code=OTHER_CODE, name="رضا کریمی")
        appointment(Doctor.objects.get(), name="مریم رضایی", days=1)
        LabPatient.objects.update(patient=None)
        Appointment.objects.update(patient=None)
        Patient.objects.all().delete()
        link(global_apps, None)
        self.assertEqual(Patient.objects.count(), 2)
        maryam = Patient.objects.get(national_code=NATIONAL_CODE)
        self.assertTrue(maryam.is_verified)
        self.assertEqual(maryam.appointments.count(), 1)
        self.assertEqual(LabPatient.objects.get().patient, maryam)
        reza = Patient.objects.get(national_code=OTHER_CODE)
        self.assertEqual((reza.first_name, reza.last_name, reza.is_verified), ("رضا", "کریمی", False))
        self.assertEqual(len({p.file_number for p in Patient.objects.all()}), 2)


class TimelineTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed()

    def test_lab_pharmacy_equipment_and_appointments_in_one_timeline(self):
        reception = staff("rec", "پذیرش آزمایشگاه")
        director = staff("dir", "مسئول فنی آزمایشگاه")
        lp = lab_patient()
        p = lp.patient
        appointment(doctor())
        order = make_order(reception, lp, ["FBS", "HBA1C"])
        for specimen in order.specimens.all():
            lis.collect_specimen(specimen, reception)
        fbs = LabResult.objects.get(item__order=order, item__test__code="FBS")
        lis.approve_result(lis.enter_result(fbs, "130", director), director)

        pharmacist = staff("pharm", "داروساز مسئول فنی")
        metformin = drug("metformin", name="متفورمین ۵۰۰", price=100_000)
        glucometer = device("glucometer", name="دستگاه تست قند", track_serial=False)
        receive(metformin, 10, 50_000)
        receive(glucometer, 2, 1_000_000, batch="G1")
        session = open_session(pharmacist)
        invoice = pos_sale(session, pharmacist, [(metformin, 2), (glucometer, 1)], patient=p)
        ret = SalesReturn.objects.create(invoice=invoice, reason="اشتباه", created_by=pharmacist, session=session)
        SalesReturnLine.objects.create(document=ret, invoice_line=invoice.lines.get(product=metformin), quantity=1)
        ret.post(pharmacist)
        pos_sale(session, pharmacist, [(metformin, 1)])  # فروش بدون کد ملی در پرونده نمی‌آید

        events = services.timeline(p)
        kinds = sorted(e.kind for e in events)
        self.assertEqual(kinds, ["appointment", "drug", "drug", "equipment", "lab"])
        lab = next(e for e in events if e.kind == "lab")
        self.assertIn("در انتظار جواب", " ".join(lab.lines))
        self.assertEqual(sum("130" in line for line in lab.lines), 1)
        drug_lines = [line for e in events if e.kind == "drug" for line in e.lines]
        self.assertIn("متفورمین ۵۰۰ × 2", drug_lines)
        self.assertIn("متفورمین ۵۰۰ × 1", drug_lines)
        self.assertEqual([e.kind for e in services.timeline(p, "equipment")], ["equipment"])

    def test_unapproved_results_are_hidden(self):
        reception = staff("rec", "پذیرش آزمایشگاه")
        tech = staff("tech", "کارشناس آزمایشگاه")
        lp = lab_patient()
        order = make_order(reception, lp, ["FBS"])
        for specimen in order.specimens.all():
            lis.collect_specimen(specimen, reception)
        lis.enter_result(LabResult.objects.get(item__order=order), "777", tech)
        lines = services.timeline(lp.patient)[0].lines
        self.assertNotIn("777", " ".join(lines))


class AccessTests(TestCase):
    def setUp(self):
        self.doctor_user = staff("doc", "پزشک")
        self.other_doctor_user = staff("doc2", "پزشک")
        self.reception = staff("front", "پذیرش درمانگاه")
        self.director = staff("med", "مسئول فنی درمانگاه")
        self.doc = doctor(self.doctor_user)
        doctor(self.other_doctor_user, slug="dr-b", council="22222")
        self.patient = appointment(self.doc).patient

    def chart(self):
        return self.client.get(reverse("ehr:chart", args=[self.patient.pk]))

    def test_treating_doctor_sees_record_and_view_is_logged(self):
        self.client.force_login(self.doctor_user)
        response = self.chart()
        self.assertContains(response, "نوبت دکتر علی محمدی")
        access = RecordAccess.objects.get()
        self.assertEqual((access.user, access.kind), (self.doctor_user, "care"))
        self.assertContains(self.client.get(reverse("ehr:my_patients")), reverse("ehr:chart", args=[self.patient.pk]))

    def test_other_doctor_must_give_reason(self):
        self.client.force_login(self.other_doctor_user)
        response = self.chart()
        self.assertContains(response, "دلیل")
        self.assertNotContains(response, "نوبت دکتر")
        self.assertFalse(RecordAccess.objects.exists())
        url = reverse("ehr:chart", args=[self.patient.pk])
        self.assertContains(self.client.post(url, {"reason": "کوتاه"}), "دلیل")
        self.assertFalse(RecordAccess.objects.exists())
        self.client.post(url, {"reason": "بیمار بدون نوبت برای ویزیت اورژانسی آمده است"})
        self.assertContains(self.chart(), "نوبت دکتر علی محمدی")
        access = RecordAccess.objects.get()
        self.assertEqual(access.kind, "emergency")
        self.assertIn("اورژانسی", access.reason)

    def test_reception_manages_demographics_but_not_clinical_record(self):
        self.client.force_login(self.reception)
        self.assertEqual(self.chart().status_code, 403)
        response = self.client.get(reverse("ehr:search"), {"q": "۰۴۹۹۳۷۰۸۹۹"})
        self.assertContains(response, self.patient.file_number)
        self.assertNotContains(response, NATIONAL_CODE)
        data = {"national_code": NATIONAL_CODE, "first_name": "مریم", "last_name": "رضایی", "sex": "f",
                "birth_date": "۱۳۶۴/۱۲/۱۰", "mobile": "09121234567"}
        self.client.post(reverse("ehr:patient_edit", args=[self.patient.pk]), data)
        self.patient.refresh_from_db()
        self.assertTrue(self.patient.is_verified)
        self.assertEqual(self.patient.birth_date, datetime.date(1986, 3, 1))
        self.assertEqual(self.client.get(reverse("ehr:access_log")).status_code, 403)

    def test_reception_creates_new_record(self):
        self.client.force_login(self.reception)
        response = self.client.post(reverse("ehr:patient_new"), {
            "foreign_id": "AF998877", "first_name": "احمد", "last_name": "حسینی", "sex": "m", "mobile": "09351234567",
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Patient.objects.get(foreign_id="AF998877").is_verified)
        response = self.client.post(reverse("ehr:patient_new"), {"first_name": "x", "last_name": "y", "sex": "m",
                                                                  "mobile": "09351234567"})
        self.assertContains(response, "کد ملی یا")

    def test_director_sees_all_records_and_access_log(self):
        self.client.force_login(self.director)
        self.assertEqual(self.chart().status_code, 200)
        self.assertEqual(RecordAccess.objects.get().kind, "all")
        self.assertContains(self.client.get(reverse("ehr:access_log")), self.patient.file_number)

    def test_anonymous_and_patient_accounts_cannot_open_records(self):
        self.assertEqual(self.chart().status_code, 302)
        from django.contrib.auth import get_user_model

        get_user_model().objects.create_user("p", password="x-strong-pass-1")
        self.client.login(username="p", password="x-strong-pass-1")
        self.assertEqual(self.chart().status_code, 302)

    def test_access_log_is_immutable(self):
        self.client.force_login(self.director)
        self.chart()
        access = RecordAccess.objects.get()
        with self.assertRaises(PermissionError):
            access.delete()


class PosLinkTests(TestCase):
    def test_checkout_with_patient_national_code_links_invoice(self):
        from apps.backoffice import demo
        from apps.sales.models import CashSession, SalesInvoice

        cashier = staff("cashier", "صندوق‌دار")
        product = drug("otc", price=150_000, sku="D-1")
        receive(product, 5, 100_000)
        self.client.force_login(cashier)
        register = demo.CashRegister.objects.create(name="صندوق ۱", warehouse=demo.Warehouse.objects.get(code="MAIN"))
        self.client.post(reverse("backoffice:pos"), {"register": register.pk, "opening_cash": 0})
        CashSession.objects.get(cashier=cashier, status="open")
        self.client.post(reverse("backoffice:pos_add"), {"code": "D-1", "quantity": "1"})
        invoice = SalesInvoice.objects.get()
        data = {"token": invoice.client_token, "discount": 0, "cash": 150_000, "card": 0, "credit": 0,
                "insurance_amount": 0, "patient_national_code": "0499370898"}
        self.client.post(reverse("backoffice:pos_checkout"), data)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "draft")  # کد ملی نامعتبر
        data["patient_national_code"] = "۰۴۹۹۳۷۰۸۹۹"
        self.client.post(reverse("backoffice:pos_checkout"), data)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "posted")
        self.assertEqual(invoice.patient.national_code, NATIONAL_CODE)
        self.assertFalse(invoice.patient.is_verified)
        self.assertEqual(SalesInvoice.objects.filter(patient__isnull=False).count(), 1)

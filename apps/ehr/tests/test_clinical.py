from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.ehr import clinical
from apps.ehr.models import Diagnosis, Encounter, ICD10Code, Problem, VitalSigns
from apps.lab.models import LabResult
from apps.lab.services import lis
from apps.lab.tests.fixtures import make_order, patient as lab_patient, seed, staff

from .test_ehr import appointment, doctor


class EncounterFlowTests(TestCase):
    def setUp(self):
        self.doctor_user = staff("doc", "پزشک")
        self.nurse = staff("nurse", "پرستار")
        self.other = staff("doc2", "پزشک")
        self.doc = doctor(self.doctor_user)
        doctor(self.other, slug="dr-b", council="22222")
        self.appt = appointment(self.doc)
        self.patient = self.appt.patient

    def start(self):
        self.client.force_login(self.doctor_user)
        self.client.post(reverse("ehr:encounter_start", args=[self.patient.pk]), {"appointment": self.appt.pk})
        return Encounter.objects.get()

    def test_full_visit_soap_vitals_diagnosis_sign(self):
        encounter = self.start()
        self.assertEqual(encounter.appointment, self.appt)
        url = reverse("ehr:encounter", args=[encounter.pk])
        self.client.post(url, {"e-kind": "visit", "e-chief_complaint": "سردرد و تشنگی", "e-subjective": "از دو هفته پیش",
                               "e-plan": "رژیم و ورزش", "e-next_visit": "۱۴۰۵/۰۹/۰۱"})
        encounter.refresh_from_db()
        self.assertEqual(encounter.chief_complaint, "سردرد و تشنگی")
        self.assertEqual(encounter.next_visit.isoformat(), "2026-11-22")
        self.client.post(reverse("ehr:encounter_vitals", args=[encounter.pk]),
                         {"v-systolic": "۱۵۵", "v-diastolic": "95", "v-weight": "92", "v-height": "170", "v-glucose": "180",
                          "v-glucose_kind": "fasting"})
        vitals = VitalSigns.objects.get()
        self.assertEqual((vitals.bp, str(vitals.bmi)), ("155/95", "31.8"))
        self.client.post(reverse("ehr:encounter_diagnosis", args=[encounter.pk]),
                         {"d-icd_code": "e11.9", "d-is_primary": "on", "d-certainty": "confirmed", "d-add_problem": "1"})
        self.client.post(reverse("ehr:encounter_diagnosis", args=[encounter.pk]),
                         {"d-icd_code": "I10", "d-certainty": "confirmed", "d-add_problem": "1"})
        self.client.post(reverse("ehr:encounter_diagnosis", args=[encounter.pk]), {"d-icd_code": "ZZ9", "d-certainty": "confirmed"})
        self.assertEqual(Diagnosis.objects.count(), 2)
        self.assertEqual(sorted(Problem.objects.values_list("chronic_program", flat=True)), ["diabetes", "hypertension"])
        self.client.post(reverse("ehr:encounter_sign", args=[encounter.pk]))
        encounter.refresh_from_db()
        self.appt.refresh_from_db()
        self.assertEqual((encounter.status, self.appt.status), ("signed", "done"))
        # پس از امضا: ویرایش و افزودن تشخیص ممکن نیست؛ فقط یادداشت تکمیلی
        with self.assertRaises(ValidationError):
            encounter.chief_complaint = "تغییر"
            encounter.save()
        with self.assertRaises(ValidationError):
            Diagnosis.objects.first().delete()
        self.assertEqual(self.client.post(reverse("ehr:encounter_diagnosis", args=[encounter.pk]), {"d-text": "x"}).status_code, 403)
        self.client.post(reverse("ehr:encounter_addendum", args=[encounter.pk]), {"text": "جواب آزمایش دیده شد"})
        self.assertEqual(encounter.addenda.count(), 1)
        chart = self.client.get(reverse("ehr:chart", args=[self.patient.pk]))
        self.assertContains(chart, "E11.9")
        self.assertContains(chart, "155/95")
        self.assertContains(chart, "کنترل‌نشده")
        self.assertContains(chart, "1 یادداشت تکمیلی")

    def test_sign_requires_content_and_owner(self):
        encounter = self.start()
        self.client.post(reverse("ehr:encounter_sign", args=[encounter.pk]))
        encounter.refresh_from_db()
        self.assertEqual(encounter.status, "open")
        # پزشک دیگر حتی با ذکر دلیل فقط می‌بیند و نمی‌نویسد
        self.client.force_login(self.other)
        self.client.post(reverse("ehr:chart", args=[self.patient.pk]), {"reason": "مشاوره همکار درباره این بیمار"})
        self.assertEqual(self.client.get(reverse("ehr:encounter", args=[encounter.pk])).status_code, 200)
        self.assertEqual(self.client.post(reverse("ehr:encounter", args=[encounter.pk]), {"e-kind": "visit"}).status_code, 403)
        self.assertEqual(self.client.post(reverse("ehr:encounter_sign", args=[encounter.pk])).status_code, 302)
        encounter.refresh_from_db()
        self.assertEqual(encounter.status, "open")

    def test_other_doctor_without_relationship_cannot_open_encounter(self):
        encounter = self.start()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("ehr:encounter", args=[encounter.pk])).status_code, 403)

    def test_referral_gives_target_doctor_access(self):
        third = staff("doc3", "پزشک")
        target = doctor(third, slug="dr-c", council="33333")
        encounter = self.start()
        self.client.post(reverse("ehr:encounter_referral", args=[encounter.pk]), {"r-to_provider": target.pk, "r-reason": "ارزیابی قلب"})
        self.client.force_login(third)
        self.assertContains(self.client.get(reverse("ehr:chart", args=[self.patient.pk])), "ارزیابی قلب")

    def test_nurse_records_vitals_and_allergy_for_todays_patient(self):
        encounter = self.start()
        self.client.force_login(self.nurse)
        self.client.post(reverse("ehr:encounter_vitals", args=[encounter.pk]), {"v-pulse": "80", "v-spo2": "98"})
        self.assertEqual(VitalSigns.objects.get().recorded_by, self.nurse)
        self.client.post(reverse("ehr:chart_add", args=[self.patient.pk, "allergy"]),
                         {"allergy-kind": "drug", "allergy-substance": "Penicillin", "allergy-severity": "severe"})
        self.assertEqual(self.patient.allergies.get().substance, "Penicillin")
        response = self.client.post(reverse("ehr:chart_add", args=[self.patient.pk, "problem"]), {"problem-title": "x"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.post(reverse("ehr:encounter_sign", args=[encounter.pk])).status_code, 403)

    def test_vitals_validation(self):
        encounter = self.start()
        url = reverse("ehr:encounter_vitals", args=[encounter.pk])
        self.client.post(url, {"v-systolic": "120"})
        self.client.post(url, {"v-glucose": "120"})
        self.client.post(url, {})
        self.assertFalse(VitalSigns.objects.exists())

    def test_icd_search(self):
        self.client.force_login(self.doctor_user)
        data = self.client.get(reverse("ehr:icd_search"), {"q": "دیابت"}).json()["results"]
        self.assertIn("E11.9", [r["code"] for r in data])
        self.assertTrue(ICD10Code.objects.filter(code="I10").exists())


class ChronicMonitoringTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed()

    def test_hba1c_from_lab_and_registry(self):
        reception = staff("rec", "پذیرش آزمایشگاه")
        director = staff("dir", "مسئول فنی آزمایشگاه")
        manager = staff("med", "مسئول فنی درمانگاه")
        lp = lab_patient()
        p = lp.patient
        Problem.objects.create(patient=p, icd=ICD10Code.objects.get(code="E11.9"), chronic_program="diabetes", recorded_by=manager)
        for value in ("8.1", "6.4"):
            order = make_order(reception, lp, ["HBA1C"])
            for specimen in order.specimens.all():
                lis.collect_specimen(specimen, reception)
            lis.approve_result(lis.enter_result(LabResult.objects.get(item__order=order), value, director), director)
        summary = clinical.chronic_summary(p)
        self.assertEqual([r.value for r in summary["hba1c"]], [8.1, 6.4])
        self.assertTrue(summary["status"]["hba1c"])
        self.assertTrue(clinical.sparkline(summary["hba1c"]))
        self.client.force_login(manager)
        response = self.client.get(reverse("ehr:registry"), {"program": "diabetes"})
        self.assertContains(response, p.file_number)
        self.assertContains(response, "کنترل‌شده")
        self.client.force_login(staff("doc", "پزشک"))
        self.assertEqual(self.client.get(reverse("ehr:registry")).status_code, 403)

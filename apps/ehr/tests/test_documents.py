import datetime
import shutil
import tempfile

from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.ehr import clinical
from apps.ehr.models import DocumentFile, Encounter, ExternalDocument, LabRequest, RecordAccess
from apps.ehr.models.documents import private_storage
from apps.lab.models import LabOrder, LabResult, LabTest
from apps.lab.services import lis
from apps.lab.tests.fixtures import seed, staff

from .test_ehr import appointment, doctor

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100
PDF = b"%PDF-1.4\n%test"


class PrivateMediaMixin:
    def setUp(self):
        super().setUp()
        self.media = tempfile.mkdtemp()
        self._old = private_storage._wrapped
        private_storage._wrapped = FileSystemStorage(location=self.media)

    def tearDown(self):
        private_storage._wrapped = self._old
        shutil.rmtree(self.media, ignore_errors=True)
        super().tearDown()


class LabRequestTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed()

    def setUp(self):
        self.doctor_user = staff("doc", "پزشک")
        self.reception = staff("rec", "پذیرش آزمایشگاه")
        self.director = staff("dir", "مسئول فنی آزمایشگاه")
        self.doc = doctor(self.doctor_user)
        self.appt = appointment(self.doc)
        self.patient = self.appt.patient
        self.client.force_login(self.doctor_user)
        self.client.post(reverse("ehr:encounter_start", args=[self.patient.pk]), {"appointment": self.appt.pk})
        self.encounter = Encounter.objects.get()

    def test_request_to_lab_order_and_results_back_in_chart(self):
        fbs, a1c = LabTest.objects.get(code="FBS"), LabTest.objects.get(code="HBA1C")
        self.client.post(reverse("ehr:lab_request_new", args=[self.encounter.pk]),
                         {"tests": [fbs.pk, a1c.pk], "clinical_note": "مصرف متفورمین", "is_urgent": "on"})
        lab_request = LabRequest.objects.get()
        self.assertEqual(lab_request.tests.count(), 2)
        self.client.force_login(self.reception)
        self.assertContains(self.client.get(reverse("ehr:lab_request_queue")), lab_request.number)
        response = self.client.post(reverse("ehr:lab_request_queue"), {"request": lab_request.pk})
        self.assertContains(response, "کامل کنید")  # بیمار نوبت اینترنتی جنس و تاریخ تولد ندارد
        self.patient.sex, self.patient.birth_date = "f", datetime.date(1980, 1, 1)
        self.patient.save()
        response = self.client.post(reverse("ehr:lab_request_queue"), {"request": lab_request.pk})
        order = LabOrder.objects.get()
        self.assertRedirects(response, reverse("lab:staff_reception_edit", args=[order.pk]), fetch_redirect_response=False)
        self.assertEqual((order.priority, order.referring_council_no, order.patient.patient), ("urgent", "11111", self.patient))
        self.assertEqual(order.items.count(), 2)
        lab_request.refresh_from_db()
        self.assertEqual(lab_request.status, "received")
        response = self.client.post(reverse("ehr:lab_request_queue"), {"request": lab_request.pk})
        self.assertEqual(LabOrder.objects.count(), 1)
        order.post(self.reception)
        for specimen in order.specimens.all():
            lis.collect_specimen(specimen, self.reception)
        for code, value in (("FBS", "160"), ("HBA1C", "8.2")):
            result = LabResult.objects.get(item__order=order, item__test__code=code)
            lis.approve_result(lis.enter_result(result, value, self.director), self.director)
        self.client.force_login(self.doctor_user)
        page = self.client.get(reverse("ehr:patient_labs", args=[self.patient.pk]))
        self.assertContains(page, "8.2")
        self.assertEqual(clinical.chronic_summary(self.patient)["last"]["hba1c"].value, 8.2)

    def test_requires_selection_and_own_open_encounter(self):
        self.client.post(reverse("ehr:lab_request_new", args=[self.encounter.pk]), {"clinical_note": "x"})
        self.assertFalse(LabRequest.objects.exists())
        other = staff("doc2", "پزشک")
        doctor(other, slug="dr-b", council="22222")
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse("ehr:lab_request_new", args=[self.encounter.pk])).status_code, 403)


class DocumentTests(PrivateMediaMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        seed()

    def setUp(self):
        super().setUp()
        self.doctor_user = staff("doc", "پزشک")
        self.doc = doctor(self.doctor_user)
        self.patient = appointment(self.doc).patient
        self.client.force_login(self.doctor_user)

    def upload(self, files, **extra):
        data = {"kind": "lab", "title": "آزمایش بیمارستان", "source": "بیمارستان امام", "document_date": "۱۴۰۵/۰۷/۲۰",
                "files": files, "val-TOTAL_FORMS": 3, "val-INITIAL_FORMS": 0, **extra}
        return self.client.post(reverse("ehr:document_upload", args=[self.patient.pk]), data)

    def test_upload_scan_pages_with_values_and_private_serving(self):
        a1c = LabTest.objects.get(code="HBA1C")
        response = self.upload([SimpleUploadedFile("scan.png", PNG, "image/png"), SimpleUploadedFile("p2.pdf", PDF, "application/pdf")],
                               **{"val-0-test": a1c.pk, "val-0-value": "۷.۵", "val-0-unit": "%"})
        document = ExternalDocument.objects.get()
        self.assertRedirects(response, reverse("ehr:document", args=[document.pk]))
        files = list(document.files.all())
        self.assertEqual([f.page for f in files], [1, 2])
        self.assertNotIn("scan", files[0].file.name)  # نام فایل تصادفی است
        self.assertNotIn(self.patient.national_code, files[0].file.name)
        self.assertEqual(clinical.chronic_summary(self.patient)["last"]["hba1c"].value, 7.5)
        before = RecordAccess.objects.count()
        response = self.client.get(reverse("ehr:document_file", args=[files[0].pk]))
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(b"".join(response.streaming_content), PNG)
        self.assertEqual(RecordAccess.objects.count(), before + 1)
        self.assertContains(self.client.get(reverse("ehr:chart", args=[self.patient.pk])), "آزمایش بیمارستان")
        # کاربر بدون رابطه درمانی فایل را نمی‌بیند
        other = staff("doc2", "پزشک")
        doctor(other, slug="dr-b", council="22222")
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse("ehr:document_file", args=[files[0].pk])).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("ehr:document_file", args=[files[0].pk])).status_code, 302)

    def test_rejects_wrong_types_and_fake_content(self):
        self.upload([SimpleUploadedFile("x.exe", b"MZ....", "application/octet-stream")])
        self.upload([SimpleUploadedFile("fake.pdf", b"<html>", "application/pdf")])
        self.assertFalse(ExternalDocument.objects.exists())

    def test_void_instead_of_delete(self):
        self.upload([SimpleUploadedFile("a.jpg", b"\xff\xd8\xff\xe0" + b"0" * 50, "image/jpeg")])
        document = ExternalDocument.objects.get()
        with self.assertRaises(PermissionError):
            document.delete()
        with self.assertRaises(PermissionError):
            DocumentFile.objects.get().delete()
        self.client.post(reverse("ehr:document_void", args=[document.pk]), {"reason": "مربوط به بیمار دیگر"})
        document.refresh_from_db()
        self.assertTrue(document.is_void)

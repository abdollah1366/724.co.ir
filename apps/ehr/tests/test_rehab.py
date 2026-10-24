from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.clinic.models import ProviderType
from apps.ehr import rehab, services
from apps.ehr.models import Assessment, Prescription, Referral, TherapySession, TreatmentPlan
from apps.lab.tests.fixtures import staff

from .test_ehr import appointment, doctor


def therapist(user, kind, slug, council):
    provider = doctor(user, slug=slug, council=council)
    provider.provider_type = kind
    provider.save()
    return provider


class RehabFlowTests(TestCase):
    def setUp(self):
        self.doctor_user = staff("doc", "پزشک")
        self.doc = doctor(self.doctor_user)
        self.patient = appointment(self.doc).patient
        self.physio_user = staff("physio", "درمانگر توانبخشی")
        self.physio = therapist(self.physio_user, ProviderType.PHYSIOTHERAPIST, "pt", "PT1")
        self.psych_user = staff("psych", "روانشناس")
        self.psych = therapist(self.psych_user, ProviderType.PSYCHOLOGIST, "psy", "PS1")
        self.audio_user = staff("audio", "درمانگر توانبخشی")
        self.audio = therapist(self.audio_user, ProviderType.AUDIOLOGIST, "aud", "AU1")

    def refer(self, to):
        return Referral.objects.create(patient=self.patient, from_provider=self.doc, to_provider=to, reason="کمردرد مزمن",
                                       created_by=self.doctor_user)

    def new_plan(self, user, **extra):
        self.client.force_login(user)
        data = {"discipline": "physio", "diagnosis": "کمردرد", "goals": "کاهش درد", "planned_sessions": "10",
                "start_date": "۱۴۰۵/۰۸/۰۱", **extra}
        return self.client.post(reverse("ehr:plan_new", args=[self.patient.pk]), data)

    def test_referral_gives_access_and_plan_closes_referral(self):
        referral = self.refer(self.physio)
        self.client.force_login(self.physio_user)
        page = self.client.get(reverse("ehr:plan_new", args=[self.patient.pk]))
        self.assertContains(page, "کمردرد مزمن")
        response = self.new_plan(self.physio_user, referral=referral.pk, icd_code="M54.5")
        plan = TreatmentPlan.objects.get()
        self.assertRedirects(response, reverse("ehr:plan", args=[plan.pk]))
        self.assertEqual((plan.therapist, plan.icd.code, plan.share_with_physicians), (self.physio, "M54.5", False))
        referral.refresh_from_db()
        self.assertEqual(referral.status, "done")

    def test_no_relationship_no_plan(self):
        self.assertEqual(self.new_plan(self.physio_user).status_code, 403)

    def test_sessions_sign_lock_and_timeline(self):
        self.refer(self.physio)
        self.new_plan(self.physio_user)
        plan = TreatmentPlan.objects.get()
        url = reverse("ehr:plan_session", args=[plan.pk])
        self.client.post(url, {"s-attendance": "attended", "s-intervention": "TENS و تمرین", "s-pain_score": "۷",
                               "s-sector": "private", "sign": "1"})
        self.client.post(url, {"s-attendance": "attended", "s-response": "بهتر", "s-pain_score": "4", "s-sector": "private"})
        self.client.post(url, {"s-attendance": "attended", "s-pain_score": "11", "s-sector": "private"})
        first, second = TherapySession.objects.order_by("number")
        self.assertEqual(TherapySession.objects.count(), 2)
        self.assertEqual((first.number, first.pain_score, bool(first.signed_at), bool(second.signed_at)), (1, 7, True, False))
        with self.assertRaises(ValidationError):
            first.response = "x"
            first.save()
        with self.assertRaises(PermissionError):
            first.delete()
        self.client.post(reverse("ehr:session_sign", args=[second.pk]))
        second.refresh_from_db()
        self.assertTrue(second.signed_at)
        self.assertEqual(plan.done_sessions, 2)
        self.assertEqual(rehab.progress(plan)[0]["label"], "شدت درد جلسات")
        # جلسه بدون محتوا امضا نمی‌شود
        empty = TherapySession.objects.create(plan=plan, created_by=self.physio_user)
        with self.assertRaises(ValidationError):
            rehab.sign_session(empty, self.physio_user)
        # پزشک ارجاع‌دهنده پیشرفت را در پرونده می‌بیند
        self.client.force_login(self.doctor_user)
        chart = self.client.get(reverse("ehr:chart", args=[self.patient.pk]))
        self.assertContains(chart, "TENS و تمرین")
        self.assertContains(chart, "درمان‌های توانبخشی و روان در جریان")
        self.assertContains(self.client.get(reverse("ehr:plan", args=[plan.pk])), "TENS و تمرین")
        # فقط درمانگر برنامه جلسه ثبت می‌کند
        self.assertEqual(self.client.post(url, {"s-attendance": "attended", "s-sector": "private"}).status_code, 403)

    def test_psychology_notes_confidential(self):
        self.refer(self.psych)
        self.client.force_login(self.psych_user)
        page = self.client.get(reverse("ehr:plan_new", args=[self.patient.pk]))
        self.assertFalse(page.context["form"].initial["share_with_physicians"])
        self.new_plan(self.psych_user, discipline="psychology", diagnosis="افسردگی", goals="بهبود خلق")
        plan = TreatmentPlan.objects.get()
        self.assertTrue(plan.is_confidential)
        self.client.post(reverse("ehr:plan_session", args=[plan.pk]),
                         {"s-attendance": "attended", "s-intervention": "رازگویی-خصوصی", "s-sector": "private", "sign": "1"})
        self.client.post(reverse("ehr:plan_assessment", args=[plan.pk]), {"a-kind": "phq9", "a-score": "16"})
        self.assertEqual(Assessment.objects.get().severity, "نسبتاً شدید")
        self.assertContains(self.client.get(reverse("ehr:plan", args=[plan.pk])), "رازگویی-خصوصی")
        # پزشک معالج: جلسه را می‌بیند، متن را نه
        self.client.force_login(self.doctor_user)
        chart = self.client.get(reverse("ehr:chart", args=[self.patient.pk]))
        self.assertNotContains(chart, "رازگویی-خصوصی")
        self.assertNotContains(chart, "افسردگی")
        self.assertContains(chart, "محرمانه")
        detail = self.client.get(reverse("ehr:plan", args=[plan.pk]))
        self.assertNotContains(detail, "رازگویی-خصوصی")
        self.assertNotContains(detail, "PHQ-9")
        # مسئول فنی می‌بیند
        head = staff("head", "مسئول فنی درمانگاه")
        self.client.force_login(head)
        self.assertContains(self.client.get(reverse("ehr:plan", args=[plan.pk])), "رازگویی-خصوصی")
        # بدون کاربر (پیش‌فرض امن) متن نمایش داده نمی‌شود
        self.assertFalse(any("رازگویی" in line for e in services.timeline(self.patient) for line in e.lines))

    def test_audiogram_and_score_ranges(self):
        self.refer(self.audio)
        self.new_plan(self.audio_user, discipline="audiology", diagnosis="کم‌شنوایی", goals="تجویز سمعک")
        plan = TreatmentPlan.objects.get()
        values = {"a-kind": "audiogram"}
        for ear, base in (("right", 20), ("left", 50)):
            for freq in (250, 500, 1000, 2000, 4000, 8000):
                values[f"a-{ear}_{freq}"] = str(base + (10 if freq == 4000 else 0))
        self.client.post(reverse("ehr:plan_assessment", args=[plan.pk]), values)
        a = Assessment.objects.get()
        self.assertEqual((a.pta("right"), a.pta("left")), (22.5, 52.5))
        self.assertIn("PTA راست 22.5", a.summary)
        page = self.client.get(reverse("ehr:plan", args=[plan.pk]))
        self.assertContains(page, "گوش چپ")
        self.client.post(reverse("ehr:plan_assessment", args=[plan.pk]), {"a-kind": "audiogram", "a-right_500": "200"})
        self.client.post(reverse("ehr:plan_assessment", args=[plan.pk]), {"a-kind": "audiogram"})
        # PHQ-9 برای شنوایی‌شناسی در فهرست نیست
        self.client.post(reverse("ehr:plan_assessment", args=[plan.pk]), {"a-kind": "phq9", "a-score": "5"})
        self.assertEqual(Assessment.objects.count(), 1)
        with self.assertRaises(ValidationError):
            rehab.add_assessment(plan, self.audio_user, "vas", 12)

    def test_close_plan_blocks_new_sessions(self):
        self.refer(self.physio)
        self.new_plan(self.physio_user)
        plan = TreatmentPlan.objects.get()
        self.client.post(reverse("ehr:plan_close", args=[plan.pk]), {"c-status": "completed", "c-summary": "درد برطرف شد"})
        plan.refresh_from_db()
        self.assertEqual(plan.status, "completed")
        response = self.client.post(reverse("ehr:plan_session", args=[plan.pk]), {"s-attendance": "attended", "s-sector": "private"})
        self.assertEqual(response.status_code, 403)
        self.assertContains(self.client.get(reverse("ehr:my_plans")), "برنامه درمان فعالی ندارید")

    def test_non_prescriber_cannot_prescribe(self):
        self.refer(self.physio)
        self.physio_user.user_permissions.add(*__import__("django.contrib.auth.models", fromlist=["Permission"]).Permission
                                              .objects.filter(codename="add_prescription"))
        self.client.force_login(self.physio_user)
        self.client.post(reverse("ehr:prescription_new", args=[self.patient.pk]))
        self.assertFalse(Prescription.objects.exists())
        self.assertFalse(self.physio.can_prescribe)
        self.assertTrue(self.doc.can_prescribe)

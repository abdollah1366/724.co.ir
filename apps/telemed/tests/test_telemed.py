import contextlib
import datetime
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Profile
from apps.audit.models import AuditLog
from apps.core import sms
from apps.ehr.tests.test_documents import PNG, PrivateMediaMixin
from apps.ehr.tests.test_ehr import appointment, doctor as make_doctor
from apps.lab.tests.fixtures import staff
from apps.telemed import services
from apps.telemed.models import ConsultAttachment, ConsultMessage, ConsultPayment, Consultation, DoctorOnlineProfile, OnlineHours

Status = Consultation.Status
Kind = Consultation.Kind

DATA = {"subject": "سرفه خشک", "for_whom": "self", "age": "34", "sex": "f", "sms_opt_in": "on", "consent": "on",
        "description": "از یک هفته پیش سرفه خشک دارم و شب‌ها بدتر می‌شود. بیماری زمینه‌ای ندارم."}


def payload(**kw):
    data = {"subject": "سرفه", "for_whom": "self", "age": 34, "sex": "f", "sms_opt_in": True,
            "description": "از یک هفته پیش سرفه خشک دارم و شب‌ها بدتر می‌شود."}
    data.update(kw)
    return data


def tomorrow_at(hour=10, minute=0):
    day = timezone.localdate() + datetime.timedelta(days=1)
    return timezone.make_aware(datetime.datetime.combine(day, datetime.time(hour, minute)))


class Base(TestCase):
    @contextlib.contextmanager
    def raises(self, text):
        with self.assertRaises(ValidationError) as cm:
            yield
        self.assertIn(text, " ".join(cm.exception.messages))

    def setUp(self):
        sms.outbox.clear()
        self.doctor_user = staff("drali", "پزشک")
        Profile.objects.create(user=self.doctor_user, mobile="09120000009")
        self.doctor = make_doctor(user=self.doctor_user)
        self.profile = DoctorOnlineProfile.objects.create(
            doctor=self.doctor, is_enabled=True, free_enabled=True, free_daily_limit=2, text_fee=1_500_000,
            call_fee=2_000_000, video_fee=2_500_000, slot_minutes=15, response_hours=24,
        )
        for weekday in range(7):
            OnlineHours.objects.create(doctor=self.doctor, weekday=weekday, start_time=datetime.time(9), end_time=datetime.time(12))
        self.patient = get_user_model().objects.create_user("patient", password="x-strong-pass-1")
        Profile.objects.create(user=self.patient, mobile="09121234567")
        self.pharmacist = staff("pharm", "داروساز مسئول فنی")

    def consult(self, kind=Kind.FREE, doctor="default", **kw):
        doctor = self.doctor if doctor == "default" else doctor
        return services.create(self.patient, kind=kind, doctor=doctor, data=payload(), **kw)


class PublicPagesTests(Base):
    def test_index_lists_enabled_doctor_and_pharmacy(self):
        other = make_doctor(slug="dr-b", council="22222")
        DoctorOnlineProfile.objects.create(doctor=other, is_enabled=False, free_enabled=True)
        page = self.client.get(reverse("telemed:index"))
        self.assertContains(page, self.doctor.full_name)
        self.assertContains(page, "مشاوره دارویی رایگان")
        self.assertContains(page, "۱۱۵")
        self.assertNotContains(page, reverse("telemed:doctor", args=["dr-b"]))
        self.assertEqual(self.client.get(reverse("telemed:doctor", args=["dr-b"])).status_code, 404)

    def test_doctor_page_shows_fees_and_free_slots(self):
        page = self.client.get(reverse("telemed:doctor", args=[self.doctor.slug]) + f"?date={tomorrow_at().date()}")
        self.assertContains(page, "پرسش متنی رایگان")
        self.assertContains(page, "ویزیت تلفنی در این ساعت")
        self.assertContains(page, f'value="{tomorrow_at().date()}T09:00"')
        self.assertContains(page, f'value="{tomorrow_at().date()}T11:45"')
        self.assertNotContains(page, f'value="{tomorrow_at().date()}T12:00"')

    def test_doctor_detail_links_to_online(self):
        self.assertContains(self.client.get(self.doctor.get_absolute_url()), reverse("telemed:doctor", args=[self.doctor.slug]))

    def test_creating_requires_login(self):
        url = reverse("telemed:new_doctor", args=[self.doctor.slug, "free"])
        self.assertRedirects(self.client.get(url), f"{reverse('accounts:login')}?next={url}", fetch_redirect_response=False)
        url = reverse("telemed:new_pharmacy")
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_kind_not_offered_is_404(self):
        self.profile.video_fee = None
        self.profile.save()
        self.client.force_login(self.patient)
        self.assertEqual(self.client.get(reverse("telemed:new_doctor", args=[self.doctor.slug, "video"])).status_code, 404)


class FreeConsultTests(PrivateMediaMixin, Base):
    def test_free_question_flow_with_one_followup(self):
        self.client.force_login(self.patient)
        data = {**DATA, "files": SimpleUploadedFile("lab.png", PNG, content_type="image/png")}
        response = self.client.post(reverse("telemed:new_doctor", args=[self.doctor.slug, "free"]), data)
        c = Consultation.objects.get()
        self.assertRedirects(response, c.get_absolute_url())
        self.assertEqual((c.status, c.fee, c.channel, c.kind), (Status.WAITING, 0, "doctor", "free"))
        self.assertIsNotNone(c.consent_at)
        self.assertEqual(ConsultAttachment.objects.count(), 1)
        # پیامک به پزشک بدون هیچ محتوای پزشکی
        self.assertEqual(sms.outbox[-1]["mobile"], "09120000009")
        self.assertNotIn("سرفه", sms.outbox[-1]["text"])

        self.client.force_login(self.doctor_user)
        page = self.client.get(c.get_absolute_url())
        self.assertContains(page, "شب‌ها بدتر")
        self.assertTrue(AuditLog.objects.filter(model="telemed.consultation", message="مشاهده گفتگوی مشاوره").exists())
        self.client.post(c.get_absolute_url(), {"text": "احتمالاً سرفه پس از سرماخوردگی است؛ مایعات گرم بنوشید."})
        c.refresh_from_db()
        self.assertEqual((c.status, c.taken_by), (Status.ANSWERED, self.doctor_user))
        self.assertEqual(sms.outbox[-1]["mobile"], "09121234567")
        self.assertNotIn("سرماخوردگی", sms.outbox[-1]["text"])

        self.client.force_login(self.patient)
        self.assertContains(self.client.get(c.get_absolute_url()), "مایعات گرم")
        self.client.post(c.get_absolute_url(), {"text": "اگر تب کردم چه کنم؟"})
        c.refresh_from_db()
        self.assertEqual(c.status, Status.WAITING)
        self.client.post(c.get_absolute_url(), {"text": "یک سؤال دیگر"})
        self.assertEqual(c.messages.filter(role="patient").count(), 2)  # پرسش تکمیلی دوم پذیرفته نشد

    def test_daily_free_limit_and_open_per_user(self):
        self.consult()
        self.consult()
        with self.raises("ظرفیت پرسش رایگان امروز"):
            self.consult()
        self.profile.free_daily_limit = 10
        self.profile.save()
        with self.raises("پرسش‌های رایگان باز شما"):
            self.consult()

    def test_only_patient_and_own_doctor_can_see(self):
        c = self.consult()
        other_doc_user = staff("drb", "پزشک")
        make_doctor(user=other_doc_user, slug="dr-b", council="22222")
        stranger = get_user_model().objects.create_user("x", password="x-strong-pass-1")
        manager = staff("gm", "مدیر کل")
        for user in (other_doc_user, stranger, manager, self.pharmacist):
            self.client.force_login(user)
            self.assertEqual(self.client.get(c.get_absolute_url()).status_code, 404, user.username)
            attachment = reverse("telemed:attachment", args=[1])
            self.assertEqual(self.client.get(attachment).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(c.get_absolute_url()).status_code, 302)

    def test_attachment_served_privately(self):
        c = services.create(self.patient, kind=Kind.FREE, doctor=self.doctor, data=payload(),
                            files=[SimpleUploadedFile("a.png", PNG, content_type="image/png")])
        item = ConsultAttachment.objects.get(message__consultation=c)
        self.assertTrue(item.file.name.startswith("telemed/"))
        self.assertNotIn("a.png", item.file.name)
        self.client.force_login(self.patient)
        response = self.client.get(reverse("telemed:attachment", args=[item.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        self.client.force_login(self.pharmacist)
        self.assertEqual(self.client.get(reverse("telemed:attachment", args=[item.pk])).status_code, 404)

    def test_rejects_non_medical_file(self):
        self.client.force_login(self.patient)
        data = {**DATA, "files": SimpleUploadedFile("x.exe", b"MZ", content_type="application/octet-stream")}
        response = self.client.post(reverse("telemed:new_doctor", args=[self.doctor.slug, "free"]), data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Consultation.objects.exists())

    def test_consent_required(self):
        self.client.force_login(self.patient)
        data = {k: v for k, v in DATA.items() if k != "consent"}
        self.client.post(reverse("telemed:new_doctor", args=[self.doctor.slug, "free"]), data)
        self.assertFalse(Consultation.objects.exists())

    def test_close_and_decline(self):
        c = self.consult()
        with self.raises("پیش از پایان"):
            services.close(c, self.doctor_user, "advice")
        self.client.force_login(self.doctor_user)
        self.client.post(reverse("telemed:decline", args=[c.code]), {"reason": "نیاز به معاینه حضوری دارد"})
        c.refresh_from_db()
        self.assertEqual(c.status, Status.DECLINED)
        self.client.force_login(self.patient)
        page = self.client.get(c.get_absolute_url())
        self.assertContains(page, "نیاز به معاینه حضوری دارد")
        self.assertNotContains(page, 'name="text"')

    def test_patient_cannot_use_provider_actions(self):
        c = self.consult()
        self.client.force_login(self.patient)
        self.assertEqual(self.client.post(reverse("telemed:close", args=[c.code]), {"outcome": "advice"}).status_code, 403)
        self.client.force_login(self.doctor_user)
        self.assertEqual(self.client.post(reverse("telemed:cancel", args=[c.code])).status_code, 403)

    def test_messages_are_not_deletable(self):
        c = self.consult()
        with self.assertRaises(PermissionError):
            c.messages.first().delete()


class PharmacyConsultTests(Base):
    def test_pharmacist_answers_free_drug_question(self):
        self.client.force_login(self.patient)
        self.client.post(reverse("telemed:new_pharmacy"), {**DATA, "subject": "تداخل دارو"})
        c = Consultation.objects.get()
        self.assertEqual((c.channel, c.doctor, c.fee, c.status), ("pharmacy", None, 0, Status.WAITING))
        self.client.force_login(self.pharmacist)
        inbox = self.client.get(reverse("telemed:inbox"))
        self.assertContains(inbox, "تداخل دارو")
        self.client.post(c.get_absolute_url(), {"text": "این دو دارو با هم تداخل مهمی ندارند."})
        c.refresh_from_db()
        self.assertEqual((c.status, c.taken_by), (Status.ANSWERED, self.pharmacist))
        # پزشک مشاوره دارویی را نمی‌بیند
        self.client.force_login(self.doctor_user)
        self.assertNotContains(self.client.get(reverse("telemed:inbox")), "تداخل دارو")

    def test_pharmacy_is_free_text_only(self):
        with self.assertRaises(ValidationError):
            services.create(self.patient, kind=Kind.TEXT, doctor=None, data=payload())

    def test_old_guest_form_still_works_and_links_to_chat(self):
        page = self.client.get(reverse("shop:consult"))
        self.assertContains(page, reverse("telemed:new_pharmacy"))
        self.assertContains(page, "ارسال سؤال")

    def test_inbox_requires_answer_permission(self):
        self.client.force_login(staff("cashier", "صندوق‌دار"))
        self.assertEqual(self.client.get(reverse("telemed:inbox")).status_code, 403)


@override_settings(PAYMENT_GATEWAY="sandbox")
class PaidConsultTests(Base):
    def pay(self, c, result="ok"):
        self.client.force_login(self.patient)
        response = self.client.post(reverse("telemed:pay", args=[c.code]))
        payment = ConsultPayment.objects.filter(consultation=c).latest("created_at")
        self.assertRedirects(response, reverse("telemed:sandbox_pay", args=[payment.authority]))
        self.assertContains(self.client.get(response["Location"]), "درگاه پرداخت آزمایشی")
        return self.client.get(reverse("telemed:pay_callback", args=[payment.authority]) + f"?result={result}"), payment

    def test_paid_text_chat_waits_for_payment(self):
        c = self.consult(Kind.TEXT)
        self.assertEqual((c.status, c.fee), (Status.AWAITING_PAYMENT, 1_500_000))
        self.assertEqual(sms.outbox, [])  # تا پرداخت، پزشک خبر ندارد
        self.client.force_login(self.doctor_user)
        self.assertEqual(self.client.get(c.get_absolute_url()).status_code, 404)

        _, failed = self.pay(c, "fail")
        failed.refresh_from_db()
        self.assertEqual(failed.status, ConsultPayment.Status.FAILED)
        c.refresh_from_db()
        self.assertEqual(c.status, Status.AWAITING_PAYMENT)

        response, payment = self.pay(c)
        self.assertRedirects(response, c.get_absolute_url())
        payment.refresh_from_db()
        c.refresh_from_db()
        self.assertEqual((payment.status, c.status), (ConsultPayment.Status.PAID, Status.WAITING))
        self.assertEqual(sms.outbox[-1]["mobile"], "09120000009")
        # بازگشت تکراری از درگاه اثری ندارد
        self.client.get(reverse("telemed:pay_callback", args=[payment.authority]) + "?result=fail")
        payment.refresh_from_db()
        self.assertEqual(payment.status, ConsultPayment.Status.PAID)

        # گفتگوی پولی محدودیت پرسش تکمیلی ندارد
        services.post_message(c, self.doctor_user, "provider", "لطفاً دمای بدن را بنویسید.")
        for i in range(3):
            services.post_message(c, self.patient, "patient", f"پیام {i}")
            services.post_message(c, self.doctor_user, "provider", f"پاسخ {i}")
        services.close(c, self.doctor_user, Consultation.Outcome.VISIT)
        self.client.force_login(self.patient)
        self.assertContains(self.client.get(c.get_absolute_url()), reverse("appointments:book", args=[self.doctor.slug]))

    def test_other_user_cannot_pay_callback(self):
        c = self.consult(Kind.TEXT)
        self.client.force_login(self.patient)
        self.client.post(reverse("telemed:pay", args=[c.code]))
        payment = ConsultPayment.objects.get()
        stranger = get_user_model().objects.create_user("x", password="x-strong-pass-1")
        self.client.force_login(stranger)
        self.assertEqual(self.client.get(reverse("telemed:pay_callback", args=[payment.authority]) + "?result=ok").status_code, 404)
        self.assertEqual(self.client.get(reverse("telemed:sandbox_pay", args=[payment.authority])).status_code, 404)

    def test_live_visit_slot_is_exclusive_and_freed_on_expiry(self):
        at = tomorrow_at(10, 0)
        c = self.consult(Kind.CALL, scheduled_at=at)
        self.assertNotIn(datetime.time(10), self.profile.available_slots(at.date()))
        other = get_user_model().objects.create_user("p2", password="x-strong-pass-1")
        with self.raises("دیگر آزاد نیست"):
            services.create(other, kind=Kind.VIDEO, doctor=self.doctor, data=payload(), scheduled_at=at)
        # پرداخت نشد: پس از مهلت منقضی و زمان آزاد می‌شود
        Consultation.objects.filter(pk=c.pk).update(created_at=timezone.now() - datetime.timedelta(minutes=31))
        self.assertEqual(services.tick()["unpaid_expired"], 1)
        self.assertIn(datetime.time(10), self.profile.available_slots(at.date()))
        services.create(other, kind=Kind.VIDEO, doctor=self.doctor, data=payload(), scheduled_at=at)

    def test_db_constraint_blocks_double_booking(self):
        at = tomorrow_at(10, 0)
        self.consult(Kind.CALL, scheduled_at=at)
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError), transaction.atomic():
            Consultation.objects.create(patient=self.patient, doctor=self.doctor, kind=Kind.VIDEO, fee=1, scheduled_at=at,
                                        status=Status.WAITING, subject="x", age=1, sex="m", consent_at=timezone.now())

    def test_in_person_appointment_time_not_offered(self):
        at = tomorrow_at(9, 30)
        appointment(self.doctor, days=1).__class__.objects.filter(doctor=self.doctor).update(time=datetime.time(9, 30))
        self.assertNotIn(datetime.time(9, 30), self.profile.available_slots(at.date()))

    def test_payment_after_expiry_becomes_refund_due(self):
        c = self.consult(Kind.TEXT)
        self.client.force_login(self.patient)
        self.client.post(reverse("telemed:pay", args=[c.code]))
        payment = ConsultPayment.objects.get()
        Consultation.objects.filter(pk=c.pk).update(status=Status.EXPIRED)
        self.client.get(reverse("telemed:pay_callback", args=[payment.authority]) + "?result=ok")
        payment.refresh_from_db()
        self.assertEqual(payment.status, ConsultPayment.Status.REFUND_DUE)

    def test_unanswered_paid_consult_expires_with_refund_due(self):
        c = self.consult(Kind.TEXT)
        self.pay(c)
        Consultation.objects.filter(pk=c.pk).update(submitted_at=timezone.now() - datetime.timedelta(hours=25))
        out = StringIO()
        call_command("telemed_tick", stdout=out)
        self.assertIn("unanswered_expired=1", out.getvalue())
        c.refresh_from_db()
        self.assertEqual(c.status, Status.EXPIRED)
        payment = ConsultPayment.objects.get(status=ConsultPayment.Status.REFUND_DUE)
        accountant = staff("acc", "حسابدار")
        services.mark_refunded(payment, accountant, "واریز به کارت")
        payment.refresh_from_db()
        self.assertEqual(payment.status, ConsultPayment.Status.REFUNDED)

    def test_cancel_live_visit_rules(self):
        c = self.consult(Kind.VIDEO, scheduled_at=tomorrow_at(11))
        self.pay(c)
        c.refresh_from_db()
        self.assertTrue(services.can_cancel(c))
        self.client.force_login(self.patient)
        self.client.post(reverse("telemed:cancel", args=[c.code]))
        c.refresh_from_db()
        self.assertEqual(c.status, Status.CANCELLED)
        self.assertTrue(ConsultPayment.objects.filter(status=ConsultPayment.Status.REFUND_DUE).exists())
        soon = timezone.localtime() + datetime.timedelta(hours=2)
        late = Consultation(kind=Kind.CALL, status=Status.WAITING, scheduled_at=soon)
        self.assertFalse(services.can_cancel(late))

    def test_video_link_and_reminder(self):
        c = self.consult(Kind.VIDEO, scheduled_at=tomorrow_at(11))
        self.pay(c)
        self.client.force_login(self.doctor_user)
        self.client.post(reverse("telemed:meeting_link", args=[c.code]), {"meeting_link": "http://insecure.example"})
        c.refresh_from_db()
        self.assertEqual(c.meeting_link, "")
        self.client.post(reverse("telemed:meeting_link", args=[c.code]), {"meeting_link": "https://meet.example/abc"})
        c.refresh_from_db()
        self.assertEqual(c.meeting_link, "https://meet.example/abc")
        self.client.force_login(self.patient)
        self.assertContains(self.client.get(c.get_absolute_url()), "ورود به تماس")
        stats = services.tick(now=c.scheduled_at - datetime.timedelta(minutes=30))
        self.assertEqual(stats["reminded"], 1)
        self.assertEqual(services.tick(now=c.scheduled_at - datetime.timedelta(minutes=20))["reminded"], 0)
        # پزشک پس از تماس، مشاوره را می‌بندد (بدون پیام متنی هم ممکن است)
        services.close(c, self.doctor_user, Consultation.Outcome.ADVICE)

    def test_auto_close_idle_chat(self):
        c = self.consult(Kind.TEXT)
        self.pay(c)
        services.post_message(c, self.doctor_user, "provider", "پاسخ")
        ConsultMessage.objects.filter(consultation=c).update(created_at=timezone.now() - datetime.timedelta(days=4))
        self.assertEqual(services.tick()["auto_closed"], 1)
        c.refresh_from_db()
        self.assertEqual(c.status, Status.CLOSED)


class NoGatewayTests(Base):
    """پیش‌فرض محیط واقعی: پرداخت اینترنتی خاموش و ثبت دستی در پنل."""

    @override_settings(PAYMENT_GATEWAY="none")
    def test_manual_payment_from_admin(self):
        c = self.consult(Kind.TEXT)
        self.client.force_login(self.patient)
        page = self.client.get(c.get_absolute_url())
        self.assertContains(page, "پرداخت اینترنتی هنوز فعال نشده")
        self.client.post(reverse("telemed:pay", args=[c.code]))
        c.refresh_from_db()
        self.assertEqual(c.status, Status.AWAITING_PAYMENT)

        manager = staff("gm", "مدیر کل")
        self.client.force_login(manager)
        changelist = reverse("admin:telemed_consultation_changelist")
        page = self.client.get(changelist)
        self.assertContains(page, c.code)
        self.assertNotContains(page, "سرفه")  # موضوع و متن گفتگو در پنل نیست
        self.client.post(changelist, {"action": "record_manual_payment", "_selected_action": [c.pk]})
        c.refresh_from_db()
        self.assertEqual(c.status, Status.WAITING)
        self.assertEqual(c.payments.get().method, ConsultPayment.Method.MANUAL)
        self.assertNotContains(self.client.get(reverse("admin:telemed_consultation_change", args=[c.pk])), "یک هفته")
        self.assertEqual(self.client.get(c.get_absolute_url()).status_code, 404)

    def test_report_for_manager_only(self):
        self.consult()
        self.client.force_login(staff("gm", "مدیر کل"))
        page = self.client.get(reverse("telemed:report"))
        self.assertContains(page, "پرسش متنی رایگان")
        self.assertNotContains(page, "سرفه")
        self.client.force_login(self.doctor_user)
        self.assertEqual(self.client.get(reverse("telemed:report")).status_code, 403)

    def test_dashboard_lists_my_consultations(self):
        c = self.consult()
        self.client.force_login(self.patient)
        self.assertContains(self.client.get(reverse("accounts:dashboard")), c.get_absolute_url())

    def test_sandbox_rejected_by_deploy_check(self):
        from apps.telemed.checks import payment_gateway_check

        with override_settings(PAYMENT_GATEWAY="sandbox", DEBUG=False):
            self.assertEqual([e.id for e in payment_gateway_check(None)], ["telemed.E002"])
        with override_settings(PAYMENT_GATEWAY="none", DEBUG=False):
            self.assertEqual(payment_gateway_check(None), [])

    @override_settings(PAYMENT_GATEWAY="none")
    def test_sandbox_page_off_without_sandbox(self):
        c = self.consult(Kind.TEXT)
        payment = ConsultPayment.objects.create(consultation=c, amount=c.fee, gateway="sandbox")
        self.client.force_login(self.patient)
        self.assertEqual(self.client.get(reverse("telemed:sandbox_pay", args=[payment.authority])).status_code, 404)

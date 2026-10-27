import datetime

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.conf import settings
from django.utils import timezone

from apps.accounting import services as acc
from apps.accounting.models import JournalEntry
from apps.audit.documents import DocStatus
from apps.core import events, flags
from apps.core.models import OutboxEvent
from apps.ehr import clinical
from apps.ehr.models import Encounter
from apps.ehr.tests.test_ehr import appointment, doctor as make_doctor
from apps.finance import reconcile
from apps.finance.models import Invoice, Receipt, Refund
from apps.inventory.tests.fixtures import drug, receive, staff
from apps.telemed import services as telemed
from apps.telemed.models import ConsultPayment, Consultation, DoctorOnlineProfile

from .base import FinanceTestCase

TELEMED_DATA = {"subject": "موضوع محرمانه", "for_whom": "self", "age": 30, "sex": "f", "sms_opt_in": False,
                "description": "شرح محرمانه بیماری که نباید به مالی برسد."}


class TelemedIntegrationTests(FinanceTestCase):
    def setUp(self):
        super().setUp()
        self.doctor = make_doctor(user=staff("tele-doc", "پزشک"))
        DoctorOnlineProfile.objects.create(doctor=self.doctor, is_enabled=True, text_fee=1_500_000, response_hours=24)
        self.consult_user = get_user_model().objects.create_user("tele-patient", password="x-strong-pass-1",
                                                                  first_name="زهرا", last_name="کریمی")
        self.staff_acc = staff("tele-acc", "حسابدار")

    def paid_consultation(self):
        consultation = telemed.create(self.consult_user, kind=Consultation.Kind.TEXT, doctor=self.doctor, data=TELEMED_DATA)
        payment = telemed.record_manual_payment(consultation, self.staff_acc, reference="POS-77")
        return consultation, payment

    def test_paid_consultation_creates_invoice_and_receipt_once(self):
        consultation, payment = self.paid_consultation()
        event = OutboxEvent.objects.get(event_type="telemed.payment.paid")
        self.assertNotIn("subject", event.payload)
        self.assertNotIn("محرمانه", str(event.payload))
        self.assertEqual(events.dispatch()["processed"], 1)
        invoice = Invoice.objects.get(source_type="telemed.consultation", source_id=str(consultation.pk))
        self.assertEqual((invoice.status, invoice.gross, invoice.balance_due, invoice.cost_center.code),
                         (DocStatus.POSTED, 1_500_000, 0, "TELE"))
        receipt = Receipt.objects.get(idempotency_key=f"telemed-payment-{payment.pk}")
        self.assertEqual(receipt.tenders.get().reference_no, "POS-77")
        # رویداد تکراری (مثلاً بازپخش دستی) سند دوباره نمی‌سازد
        OutboxEvent.objects.filter(pk=event.pk).update(status=OutboxEvent.Status.PENDING)
        events.dispatch()
        self.assertEqual((Invoice.objects.count(), Receipt.objects.count()), (1, 1))
        self.assertEqual(acc.account_balance("4202"), -1_500_000)

    def test_unanswered_consultation_refund_flow(self):
        consultation, payment = self.paid_consultation()
        Consultation.objects.filter(pk=consultation.pk).update(submitted_at=timezone.now() - datetime.timedelta(hours=25))
        telemed.tick()
        payment.refresh_from_db()
        telemed.mark_refunded(payment, self.staff_acc, "SHABA-1")
        stats = events.dispatch()
        self.assertEqual(stats["processed"], 3, OutboxEvent.objects.values_list("event_type", "status", "last_error"))
        invoice = Invoice.objects.get()
        self.assertEqual(invoice.status, DocStatus.REVERSED)
        refund = Refund.objects.get()
        self.assertEqual((refund.status, refund.amount, refund.reference_no), (DocStatus.POSTED, 1_500_000, "SHABA-1"))
        invoice.refresh_from_db()
        self.assertEqual(invoice.balance_due, 0)
        self.assertTrue(all(c["ok"] for c in reconcile.run_checks()), reconcile.run_checks())

    def test_refund_before_payment_event_is_processed_waits(self):
        consultation, payment = self.paid_consultation()
        paid_event = OutboxEvent.objects.get(event_type="telemed.payment.paid")
        Consultation.objects.filter(pk=consultation.pk).update(submitted_at=timezone.now() - datetime.timedelta(hours=25))
        telemed.tick()
        due = OutboxEvent.objects.get(event_type="telemed.payment.refund_due")
        self.assertEqual(events.process(due.pk), OutboxEvent.Status.FAILED)  # دریافت هنوز ثبت نشده
        events.process(paid_event.pk)
        events.retry(OutboxEvent.objects.filter(pk=due.pk))
        self.assertEqual(events.process(due.pk), OutboxEvent.Status.PROCESSED)

    def test_failed_payment_has_no_financial_effect(self):
        consultation = telemed.create(self.consult_user, kind=Consultation.Kind.TEXT, doctor=self.doctor, data=TELEMED_DATA)
        payment = ConsultPayment.objects.create(consultation=consultation, amount=1_500_000, status=ConsultPayment.Status.FAILED)
        telemed._publish_payment(payment, "failed")
        events.dispatch()
        self.assertEqual(JournalEntry.objects.count(), 0)

    def test_flag_off_holds_then_reenable_bills(self):
        flags.set_flag("finance.integration.telemed", False, self.manager, "راه‌اندازی تدریجی")
        self.paid_consultation()
        self.assertEqual(events.dispatch()["processed"], 0)
        self.assertFalse(Invoice.objects.exists())
        self.assertTrue(Consultation.objects.filter(status=Consultation.Status.WAITING).exists())  # ماژول درمانی کار می‌کند
        flags.set_flag("finance.integration.telemed", True, self.manager, "روشن")
        self.assertEqual(events.dispatch()["processed"], 1)
        self.assertEqual(Invoice.objects.count(), 1)


class EncounterIntegrationTests(FinanceTestCase):
    def setUp(self):
        super().setUp()
        self.doctor_user = staff("enc-doc", "پزشک")
        self.doc = make_doctor(user=self.doctor_user)
        self.doc.visit_fee = 2_000_000
        self.doc.save()
        self.appt = appointment(self.doc)

    def sign(self):
        encounter = Encounter.objects.create(patient=self.appt.patient, provider=self.doc, appointment=self.appt,
                                             created_by=self.doctor_user, chief_complaint="سردرد محرمانه",
                                             assessment="تشخیص محرمانه")
        return clinical.sign(encounter, self.doctor_user)

    def test_signed_visit_creates_draft_invoice_with_visit_fee(self):
        encounter = self.sign()
        event = OutboxEvent.objects.get(event_type="ehr.encounter.signed")
        self.assertNotIn("محرمانه", str(event.payload))
        for key in ("chief_complaint", "assessment", "diagnoses", "subjective"):
            self.assertNotIn(key, event.payload)
        events.dispatch()
        invoice = Invoice.objects.get(source_id=str(encounter.pk))
        self.assertEqual((invoice.status, invoice.gross, invoice.patient_id, invoice.provider_id),
                         (DocStatus.DRAFT, 2_000_000, self.appt.patient_id, self.doc.pk))
        self.assertFalse(JournalEntry.objects.exists())  # پیش‌نویس تا تأیید صندوق ثبت مالی ندارد

    @override_settings(FINANCE_RULES={**settings.FINANCE_RULES, "auto_post_encounter_invoices": True})
    def test_auto_post_rule_and_no_double_billing(self):
        encounter = self.sign()
        events.dispatch()
        events.publish("ehr.encounter.signed", encounter, {**OutboxEvent.objects.get().payload}, dedupe_key="replay")
        events.dispatch()
        self.assertEqual(Invoice.objects.filter(status=DocStatus.POSTED).count(), 1)

    def test_appointment_already_billed_at_reception_is_skipped(self):
        self.invoice(patient=None, source=("appointments.appointment", self.appt.pk))
        self.sign()
        events.dispatch()
        self.assertEqual(Invoice.objects.count(), 1)
        self.assertIn("قبلاً", OutboxEvent.objects.get().result_ref)


class LedgerLinkTests(FinanceTestCase):
    def test_pharmacy_goods_receipt_event_links_existing_entry_without_new_posting(self):
        receipt = receive(drug(), 10, 100_000)
        entries = JournalEntry.objects.count()
        event = OutboxEvent.objects.get(event_type="purchasing.goodsreceipt.posted")
        self.assertEqual(event.payload["number"], receipt.number)
        events.dispatch()
        event.refresh_from_db()
        self.assertEqual(JournalEntry.objects.count(), entries)
        self.assertEqual(event.result_ref, JournalEntry.objects.get(source_type="purchasing.goodsreceipt").number)

    def test_pharmacy_flag_off_does_not_block_pharmacy(self):
        flags.set_flag("finance.integration.pharmacy", False, self.manager, "آزمون")
        receive(drug(), 5, 100_000)
        self.assertEqual(events.dispatch()["processed"], 0)
        self.assertEqual(JournalEntry.objects.filter(source_type="purchasing.goodsreceipt").count(), 1)

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, override_settings

from apps.accounting import services as acc
from apps.audit.documents import DocStatus, DocumentError, DocumentLocked
from apps.finance import reconcile
from apps.finance.models import Invoice, Refund, TenderMethod
from apps.finance.money import percent_of, prorate, split_proportional, to_rial
from apps.finance.services import billing

from .base import FinanceTestCase


class MoneyTests(SimpleTestCase):
    def test_half_up_rounding_and_sign(self):
        self.assertEqual(to_rial(Decimal("2.5")), 3)
        self.assertEqual(percent_of(1_005, Decimal("10")), 101)  # 100.5 → 101
        self.assertEqual(percent_of(-1_005, Decimal("10")), -101)
        self.assertEqual(prorate(1_000, 1, 3), 333)
        self.assertEqual(prorate(1_000, 1, 0), 0)

    def test_split_is_exact_and_deterministic(self):
        parts = split_proportional(100, [1, 1, 1])
        self.assertEqual(sum(parts), 100)
        self.assertEqual(parts, [34, 33, 33])
        self.assertEqual(parts, split_proportional(100, [1, 1, 1]))
        self.assertEqual(split_proportional(-10, [3, 7]), [-3, -7])
        self.assertEqual(split_proportional(10, [0, 0]), [0, 0])
        self.assertEqual(split_proportional(7, []), [])


class InvoiceTests(FinanceTestCase):
    def test_posting_splits_patient_insurer_discount_and_revenue(self):
        from apps.accounting.models import Party

        insurer = Party.objects.create(kind=Party.Kind.INSURER, name="بیمه آزمون")
        invoice = self.invoice(insurer=insurer, discount_reason="کارکنان", lines=[
            {"description": "ویزیت", "service_code": "VISIT", "unit_price": 5_000_000, "discount": 500_000},
            {"description": "خدمت", "service_code": "S1", "unit_price": 1_500_000, "quantity": 2, "insurer_share": 2_000_000},
        ])
        self.assertEqual((invoice.gross, invoice.discount, invoice.insurer_share, invoice.patient_share),
                         (8_000_000, 500_000, 2_000_000, 5_500_000))
        self.assertTrue(invoice.number.startswith("INV-"))
        lines = self.lines_of(self.entry_for(invoice))
        self.assertEqual(lines[acc.AR_PATIENTS], (5_500_000, 0))
        self.assertEqual(lines[acc.AR_INSURERS], (2_000_000, 0))
        self.assertEqual(lines[billing.DISCOUNT_ACCOUNT], (500_000, 0))
        self.assertEqual(lines["4201"], (0, 8_000_000))
        entry = self.entry_for(invoice)
        self.assertEqual(set(entry.lines.values_list("cost_center__code", flat=True)), {"CLINIC"})
        self.assertBalanced()

    def test_discount_needs_reason_and_cannot_exceed_gross(self):
        with self.assertRaises(DocumentError):
            self.invoice(lines=[{"description": "x", "unit_price": 100, "discount": 10}])
        with self.assertRaises(DocumentError):
            self.invoice(discount_reason="r", lines=[{"description": "x", "unit_price": 100, "discount": 101}])

    def test_one_active_invoice_per_source_and_reissue_after_reversal(self):
        first = self.invoice(source=("ehr.encounter", 7))
        with self.assertRaises((DocumentError, IntegrityError)), transaction.atomic():
            self.invoice(source=("ehr.encounter", 7))
        first.reverse(self.manager, "اشتباه در خدمت")
        again = self.invoice(source=("ehr.encounter", 7))
        self.assertEqual(again.status, DocStatus.POSTED)

    def test_posted_invoice_cannot_be_deleted_or_edited(self):
        invoice = self.invoice()
        with self.assertRaises(DocumentLocked):
            invoice.delete()
        line = invoice.lines.first()
        line.unit_price = 1
        with self.assertRaises(DocumentLocked):
            line.save()


class ReceiptTests(FinanceTestCase):
    def test_mixed_partial_then_overpayment_goes_to_advance(self):
        invoice = self.invoice(lines=[{"description": "x", "unit_price": 3_000_000}])
        receipt = billing.receive(patient_user_id=self.patient.pk, patient_name="بیمار", user=self.cashier, invoices=[invoice],
                                  tenders=[{"method": TenderMethod.CASH, "amount": 1_000_000},
                                           {"method": TenderMethod.CARD, "amount": 500_000, "reference_no": "POS-1"}])
        invoice.refresh_from_db()
        self.assertEqual((invoice.paid_amount, invoice.payment_status), (1_500_000, "پرداخت ناقص"))
        lines = self.lines_of(self.entry_for(receipt))
        self.assertEqual(lines[acc.CASH], (1_000_000, 0))
        self.assertEqual(lines[acc.BANK], (500_000, 0))
        self.assertEqual(lines[acc.AR_PATIENTS], (0, 1_500_000))
        second = self.pay(2_000_000, [invoice], TenderMethod.BANK, "TRF-1")
        invoice.refresh_from_db()
        self.assertEqual((invoice.balance_due, second.advance), (0, 500_000))
        self.assertEqual(billing.advance_balance(patient_user_id=self.patient.pk), 500_000)
        self.assertEqual(self.lines_of(self.entry_for(second))[acc.PATIENT_ADVANCES], (0, 500_000))
        self.assertTrue(all(c["ok"] for c in reconcile.run_checks()))

    def test_advance_can_pay_a_later_invoice(self):
        self.pay(1_000_000)  # پیش‌پرداخت
        invoice = self.invoice(lines=[{"description": "x", "unit_price": 800_000}])
        receipt = self.pay(800_000, [invoice], TenderMethod.ADVANCE)
        invoice.refresh_from_db()
        self.assertEqual(invoice.balance_due, 0)
        self.assertEqual(self.lines_of(self.entry_for(receipt))[acc.PATIENT_ADVANCES], (800_000, 0))
        self.assertEqual(billing.advance_balance(patient_user_id=self.patient.pk), 200_000)
        with self.assertRaises(DocumentError):
            self.pay(300_000, method=TenderMethod.ADVANCE)

    def test_same_idempotency_key_returns_the_same_receipt(self):
        invoice = self.invoice()
        first = self.pay(400_000, [invoice], key="k-123")
        second = self.pay(400_000, [invoice], key="k-123")
        self.assertEqual(first.pk, second.pk)
        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, 400_000)

    def test_card_and_bank_need_reference(self):
        with self.assertRaises(DocumentError):
            self.pay(100_000, method=TenderMethod.CARD)

    def test_cannot_allocate_more_than_balance_or_to_other_patient(self):
        invoice = self.invoice(lines=[{"description": "x", "unit_price": 100_000}])
        with self.assertRaises(DocumentError):
            billing.receive(patient_user_id=self.patient.pk, patient_name="ب", user=self.cashier,
                            tenders=[{"method": TenderMethod.CASH, "amount": 200_000}], allocations=[(invoice, 200_000)])
        from django.contrib.auth import get_user_model

        other = get_user_model().objects.create_user("other-patient")
        with self.assertRaises(DocumentError):
            billing.receive(patient_user_id=other.pk, patient_name="دیگری", user=self.cashier,
                            tenders=[{"method": TenderMethod.CASH, "amount": 100_000}], allocations=[(invoice, 100_000)])

    def test_reversing_receipt_restores_balance(self):
        invoice = self.invoice()
        receipt = self.pay(1_000_000, [invoice])
        receipt.reverse(self.manager, "ثبت اشتباه")
        invoice.refresh_from_db()
        self.assertEqual(invoice.balance_due, 1_000_000)
        self.assertBalanced()


class RefundTests(FinanceTestCase):
    def request(self, amount, invoice=None, **kw):
        return billing.request_refund(patient_user_id=self.patient.pk, patient_name="بیمار", amount=amount,
                                      method=TenderMethod.BANK, reference_no="RF-1", reason="انصراف", user=self.cashier,
                                      invoice=invoice, **kw)

    def test_four_eyes_approval_then_payment(self):
        self.pay(1_000_000)
        refund = self.request(600_000)
        with self.assertRaises(DocumentError):
            refund.post(self.cashier)  # بدون تأیید
        with self.assertRaises(DocumentError):
            billing.approve_refund(refund, self.cashier)  # مجوز ندارد
        with self.assertRaises(DocumentError):
            billing.approve_refund(refund, self.cashier.__class__.objects.get(pk=self.cashier.pk))
        billing.approve_refund(refund, self.approver)
        refund.refresh_from_db()
        refund.post(self.cashier)
        self.assertEqual(self.lines_of(self.entry_for(refund))[acc.PATIENT_ADVANCES], (600_000, 0))
        self.assertEqual(billing.advance_balance(patient_user_id=self.patient.pk), 400_000)

    def test_requester_cannot_approve_own_refund(self):
        self.pay(1_000_000)
        refund = billing.request_refund(patient_user_id=self.patient.pk, patient_name="ب", amount=100_000,
                                        method=TenderMethod.CASH, reason="r", user=self.manager)
        with self.assertRaises(DocumentError):
            billing.approve_refund(refund, self.manager)

    def test_refund_cannot_exceed_advance(self):
        self.pay(100_000)
        refund = self.request(200_000)
        billing.approve_refund(refund, self.approver)
        refund.refresh_from_db()
        with self.assertRaises(DocumentError):
            refund.post(self.cashier)

    def test_cancelled_paid_invoice_becomes_credit_and_is_refunded(self):
        invoice = self.invoice(lines=[{"description": "x", "unit_price": 900_000}])
        self.pay(900_000, [invoice])
        invoice.reverse(self.manager, "خدمت انجام نشد")
        invoice.refresh_from_db()
        self.assertEqual((invoice.balance_due, invoice.payment_status), (-900_000, "ابطال؛ قابل استرداد"))
        refund = self.request(900_000, invoice=invoice)
        billing.approve_refund(refund, self.approver)
        refund.refresh_from_db()
        refund.post(self.cashier)
        invoice.refresh_from_db()
        self.assertEqual(invoice.balance_due, 0)
        self.assertEqual(acc.account_balance(acc.AR_PATIENTS), 0)
        self.assertTrue(all(c["ok"] for c in reconcile.run_checks()), reconcile.run_checks())

    @override_settings(FINANCE_RULES={**__import__("django.conf").conf.settings.FINANCE_RULES,
                                      "refund_approval_threshold": 500_000})
    def test_small_refund_below_threshold_needs_no_approval(self):
        self.pay(1_000_000)
        refund = self.request(100_000)
        refund.post(self.cashier)
        self.assertEqual(Refund.objects.get(pk=refund.pk).status, DocStatus.POSTED)

    def test_aging_and_subledger_match_gl(self):
        self.invoice(lines=[{"description": "x", "unit_price": 700_000}])
        aging = billing.receivables_aging()
        self.assertEqual((aging["total"], aging["buckets"]["0-30"]), (700_000, 700_000))
        self.assertEqual(billing.subledger_totals()["ar_patients"], acc.account_balance(acc.AR_PATIENTS))
        self.assertEqual(Invoice.objects.count(), 1)

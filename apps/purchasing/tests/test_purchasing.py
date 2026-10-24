from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.accounting import services as acc
from apps.accounting.models import Payment
from apps.inventory import services as inv
from apps.inventory.tests.fixtures import days, drug, main, receive, shelf, staff, supplier
from apps.purchasing.models import GoodsReceipt, GoodsReceiptLine, PurchaseInvoice, PurchaseOrder, PurchaseOrderLine
from apps.purchasing.services import fill_invoice_from_receipt, supplier_payable


class PurchaseFlowTests(TestCase):
    def setUp(self):
        self.manager = staff("manager", "مدیر داروخانه")
        self.supplier = supplier()
        self.product = drug()

    def order(self, qty=10, price=100_000):
        po = PurchaseOrder.objects.create(supplier=self.supplier)
        line = PurchaseOrderLine.objects.create(document=po, product=self.product, quantity=qty, unit_price=price)
        po.post(self.manager)
        return po, line

    def receipt_for(self, po, line, qty, **kw):
        receipt = GoodsReceipt.objects.create(supplier=self.supplier, purchase_order=po, warehouse=main())
        GoodsReceiptLine.objects.create(
            document=receipt, po_line=line, product=self.product, batch_number=kw.get("batch", "B1"),
            expiry_date=days(365), location=shelf(), quantity=qty, unit_cost=kw.get("cost", line.unit_price),
            authenticity_code="UID-1", authenticity_verified=True,
        )
        return receipt

    def test_po_approval_has_no_stock_or_financial_effect(self):
        self.order()
        self.assertEqual(inv.on_hand(self.product), 0)
        self.assertFalse(acc.JournalLine.objects.exists())

    def test_receipt_against_po_and_over_receipt_blocked(self):
        po, line = self.order(qty=10)
        self.receipt_for(po, line, 6).post(self.manager)
        self.assertEqual(line.received_quantity, 6)
        with self.assertRaisesMessage(ValidationError, "بیشتر"):
            self.receipt_for(po, line, 5, batch="B2").post(self.manager)
        self.assertEqual(acc.account_balance(acc.INVENTORY), 600_000)
        self.assertEqual(acc.account_balance(acc.GRNI), -600_000)

    def test_unapproved_po_cannot_be_received(self):
        po = PurchaseOrder.objects.create(supplier=self.supplier)
        line = PurchaseOrderLine.objects.create(document=po, product=self.product, quantity=1, unit_price=1)
        with self.assertRaisesMessage(ValidationError, "تأیید"):
            self.receipt_for(po, line, 1).post(self.manager)

    def test_authenticity_check_is_required(self):
        with self.assertRaisesMessage(ValidationError, "اصالت"):
            receive(self.product, 1, 100, authenticity_verified=False)
        with self.assertRaisesMessage(ValidationError, "اصالت"):
            receive(self.product, 1, 100, authenticity_code="")

    def test_invoice_payment_and_supplier_balance(self):
        receipt = receive(self.product, 10, 100_000, user=self.manager, sup=self.supplier)
        invoice = PurchaseInvoice.objects.create(supplier=self.supplier, receipt=receipt, supplier_invoice_no="F-1",
                                                 discount=50_000, tax=0)
        fill_invoice_from_receipt(invoice)
        invoice.post(self.manager)
        self.assertEqual(acc.account_balance(acc.GRNI), 0)
        self.assertEqual(acc.account_balance(acc.PURCHASE_VARIANCE), -50_000)
        self.assertEqual(supplier_payable(self.supplier), 950_000)
        pay = Payment.objects.create(direction=Payment.Direction.PAY, party=self.supplier, method="bank", amount=400_000,
                                     reference_no="T-1")
        pay.post(self.manager)
        self.assertEqual(supplier_payable(self.supplier), 550_000)
        self.assertEqual(acc.account_balance(acc.BANK), -400_000)

    def test_duplicate_supplier_invoice_number_is_blocked(self):
        r1 = receive(self.product, 1, 100, sup=self.supplier, batch="X1")
        r2 = receive(self.product, 1, 100, sup=self.supplier, batch="X2")
        PurchaseInvoice.objects.create(supplier=self.supplier, receipt=r1, supplier_invoice_no="F-9")
        with self.assertRaises(IntegrityError), transaction.atomic():
            PurchaseInvoice.objects.create(supplier=self.supplier, receipt=r2, supplier_invoice_no="F-9")

    def test_receipt_cannot_be_invoiced_twice_and_invoice_must_match_quantities(self):
        receipt = receive(self.product, 5, 100, sup=self.supplier)
        invoice = PurchaseInvoice.objects.create(supplier=self.supplier, receipt=receipt, supplier_invoice_no="F-1")
        fill_invoice_from_receipt(invoice)
        line = invoice.lines.get()
        line.quantity = 4
        line.save()
        with self.assertRaisesMessage(ValidationError, "برابر نیست"):
            invoice.post(self.manager)
        line.quantity = 5
        line.save()
        invoice.post(self.manager)
        second = PurchaseInvoice.objects.create(supplier=self.supplier, receipt=receipt, supplier_invoice_no="F-2")
        fill_invoice_from_receipt(second)
        with self.assertRaisesMessage(ValidationError, "قبلاً"):
            second.post(self.manager)

    def test_receipt_reversal_rules(self):
        receipt = receive(self.product, 10, 100_000, sup=self.supplier)
        invoice = PurchaseInvoice.objects.create(supplier=self.supplier, receipt=receipt, supplier_invoice_no="F-1")
        fill_invoice_from_receipt(invoice)
        invoice.post(self.manager)
        with self.assertRaisesMessage(ValidationError, "فاکتور"):
            receipt.reverse(self.manager, "اشتباه")
        invoice.reverse(self.manager, "فاکتور اشتباه")
        self.assertEqual(supplier_payable(self.supplier), 0)
        receipt.reverse(self.manager, "کالا مرجوع شد")
        self.assertEqual(inv.on_hand(self.product), 0)
        self.assertEqual(acc.account_balance(acc.INVENTORY), 0)
        self.assertEqual(acc.account_balance(acc.GRNI), 0)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 0)

    def test_receipt_reversal_after_consumption_is_blocked(self):
        receipt = receive(self.product, 3, 100, sup=self.supplier)
        inv.stock_out(product=self.product, warehouse=main(), quantity=2, move_type="sale", source=("t", 1, ""), user=None)
        with self.assertRaises(inv.InsufficientStock):
            receipt.reverse(self.manager, "اشتباه")
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, "posted")
        self.assertEqual(inv.on_hand(self.product), 1)

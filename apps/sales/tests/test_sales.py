from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F
from django.test import TestCase

from apps.accounting import services as acc
from apps.accounting.models import Party
from apps.accounts.models import PointTransaction
from apps.inventory import services as inv
from apps.inventory.models import Batch
from apps.inventory.tests.fixtures import device, drug, open_session, pos_sale, quarantine, receive, staff
from apps.sales import services as sales
from apps.sales.models import SalesInvoice, SalesPayment, SalesReturn, SalesReturnLine
from apps.shop.models import Order, OrderItem, Product

M = SalesPayment.Method


class PosSaleTests(TestCase):
    def setUp(self):
        self.pharmacist = staff("pharm", "داروساز مسئول فنی")
        self.cashier = staff("cash", "صندوق‌دار")
        self.otc = drug("otc", price=150_000)
        receive(self.otc, 20, 100_000)
        self.session = open_session(self.cashier, cash=1_000_000)

    def test_cash_sale_posts_stock_and_balanced_journal(self):
        invoice = pos_sale(self.session, self.cashier, [(self.otc, 4)])
        self.assertTrue(invoice.number.startswith("SI-"))
        self.otc.refresh_from_db()
        self.assertEqual(self.otc.stock, 16)
        self.assertEqual(acc.account_balance(acc.CASH), 600_000)
        self.assertEqual(acc.account_balance(acc.SALES), -600_000)
        self.assertEqual(acc.account_balance(acc.COGS), 400_000)
        self.assertEqual(acc.account_balance(acc.INVENTORY), 1_600_000)
        self.assertEqual(invoice.lines.get().allocations.get().batch.number, "B1")
        _, totals = acc.trial_balance()
        self.assertEqual(totals["debit"], totals["credit"])

    def test_sale_requires_open_session(self):
        sales.close_session(self.session, 1_000_000, self.cashier)
        with self.assertRaisesMessage(ValidationError, "شیفت"):
            pos_sale(self.session, self.cashier, [(self.otc, 1)])

    def test_payments_must_match_total(self):
        with self.assertRaisesMessage(ValidationError, "برابر نیست"):
            pos_sale(self.session, self.cashier, [(self.otc, 1)], payments=[(M.CASH, 100)])
        self.assertEqual(inv.on_hand(self.otc), 20)
        self.assertFalse(acc.JournalLine.objects.filter(account__code=acc.SALES).exists())

    def test_prescription_drug_requires_prescription_and_pharmacist(self):
        rx = drug("rx", price=300_000, requires_prescription=True)
        receive(rx, 5, 200_000, batch="R1")
        with self.assertRaisesMessage(ValidationError, "شماره نسخه"):
            pos_sale(self.session, self.pharmacist, [(rx, 1)])
        rx_fields = {"prescription_number": "RX-1", "prescriber_name": "دکتر", "prescriber_council_no": "12345"}
        with self.assertRaisesMessage(ValidationError, "داروساز"):
            pos_sale(self.session, self.cashier, [(rx, 1)], **rx_fields)
        invoice = pos_sale(self.session, self.pharmacist, [(rx, 1)], **rx_fields)
        self.assertEqual(invoice.pharmacist_approved_by, self.pharmacist)

    def test_max_quantity_per_sale(self):
        limited = drug("limited", max_qty_per_sale=2)
        receive(limited, 10, 1_000, batch="L1")
        with self.assertRaisesMessage(ValidationError, "حداکثر"):
            pos_sale(self.session, self.cashier, [(limited, 3)])

    def test_stopped_product_cannot_be_sold(self):
        Product.objects.filter(pk=self.otc.pk).update(is_sellable=False)
        self.otc.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "قابل فروش نیست"):
            pos_sale(self.session, self.cashier, [(self.otc, 1)])

    def test_insurance_and_credit_sale(self):
        insurer = Party.objects.create(kind=Party.Kind.INSURER, name="بیمه")
        customer = Party.objects.create(kind=Party.Kind.CUSTOMER, name="مشتری")
        pos_sale(self.session, self.cashier, [(self.otc, 2)], payments=[(M.CASH, 90_000), (M.CREDIT, 0)],
                 post=False)  # پیش‌نویس رها شده، اثری ندارد
        invoice = pos_sale(
            self.session, self.cashier, [(self.otc, 2)], payments=[(M.CASH, 90_000)], insurer=insurer,
            insurance_amount=210_000, prescription_number="RX-2", prescriber_council_no="1",
        )
        self.assertEqual(acc.account_balance(acc.AR_INSURERS), 210_000)
        credit = pos_sale(self.session, self.cashier, [(self.otc, 1)], payments=[(M.CREDIT, None)], customer=customer)
        self.assertEqual(acc.account_balance(acc.AR_CUSTOMERS), 150_000)
        self.assertEqual(invoice.status, credit.status)

    def test_credit_sale_needs_customer(self):
        with self.assertRaisesMessage(ValidationError, "مشتری"):
            pos_sale(self.session, self.cashier, [(self.otc, 1)], payments=[(M.CREDIT, None)])

    def test_tax_is_added_and_posted(self):
        monitor = device(tax_rate=10)
        receive(monitor, 1, 20_000_000, batch="SN1")
        invoice = pos_sale(self.session, self.pharmacist, [(monitor, 1)])
        self.assertEqual(invoice.total, 33_000_000)
        self.assertEqual(acc.account_balance(acc.VAT_OUTPUT), -3_000_000)

    def test_duplicate_client_token_rejected(self):
        sales.create_pos_invoice(self.session, self.cashier, "tok-1")
        with self.assertRaises(IntegrityError), transaction.atomic():
            sales.create_pos_invoice(self.session, self.cashier, "tok-1")

    def test_session_close_records_shortage(self):
        pos_sale(self.session, self.cashier, [(self.otc, 2)])
        self.assertEqual(sales.session_expected_cash(self.session), 1_300_000)
        closed = sales.close_session(self.session, 1_290_000, self.cashier)
        self.assertEqual(closed.difference, -10_000)
        self.assertEqual(acc.account_balance(acc.CASH_OVER_SHORT), 10_000)
        with self.assertRaises(ValidationError):
            sales.close_session(closed, 0, self.cashier)

    def test_reverse_sale_restores_stock_and_journal(self):
        invoice = pos_sale(self.session, self.cashier, [(self.otc, 3)])
        invoice.reverse(self.cashier, "ثبت اشتباه")
        self.otc.refresh_from_db()
        self.assertEqual(self.otc.stock, 20)
        for code in (acc.CASH, acc.SALES, acc.COGS):
            self.assertEqual(acc.account_balance(code), 0)
        self.assertEqual(acc.account_balance(acc.INVENTORY), 2_000_000)


class ReturnTests(TestCase):
    def setUp(self):
        self.user = staff("pharm", "داروساز مسئول فنی")
        self.otc = drug("otc", price=150_000)
        receive(self.otc, 10, 100_000)
        self.session = open_session(self.user)
        self.invoice = pos_sale(self.session, self.user, [(self.otc, 4)])

    def make_return(self, qty, restock, refund=SalesReturn.Refund.CASH):
        ret = SalesReturn.objects.create(invoice=self.invoice, reason="مرجوعی", restock=restock, refund_method=refund,
                                         session=self.session)
        SalesReturnLine.objects.create(document=ret, invoice_line=self.invoice.lines.get(), quantity=qty)
        return ret

    def test_restock_return_goes_back_to_same_batch(self):
        self.make_return(2, restock=True).post(self.user)
        self.otc.refresh_from_db()
        self.assertEqual(self.otc.stock, 8)
        self.assertEqual(acc.account_balance(acc.SALES_RETURNS), 300_000)
        self.assertEqual(acc.account_balance(acc.COGS), 200_000)
        self.assertEqual(sales.session_expected_cash(self.session), 300_000)

    def test_opened_return_goes_to_quarantine(self):
        self.make_return(1, restock=False).post(self.user)
        self.otc.refresh_from_db()
        self.assertEqual(self.otc.stock, 6)
        self.assertEqual(inv.on_hand(self.otc, quarantine()), 1)

    def test_cannot_return_more_than_sold(self):
        self.make_return(3, restock=True).post(self.user)
        with self.assertRaisesMessage(ValidationError, "بیشتر"):
            self.make_return(2, restock=True).post(self.user)
        with self.assertRaisesMessage(ValidationError, "مرجوعی"):
            self.invoice.reverse(self.user, "x")

    def test_return_of_recalled_batch_never_restocks(self):
        batch = Batch.objects.get(product=self.otc, number="B1")
        inv.change_batch_status(batch, Batch.Status.RECALLED, "فراخوان", self.user)
        self.make_return(1, restock=True).post(self.user)
        self.assertEqual(inv.on_hand(self.otc, quarantine()), 1)


class OnlineOrderTests(TestCase):
    def setUp(self):
        self.product = drug("otc-online", price=200_000)
        receive(self.product, 10, 120_000)
        self.user = staff("09121111111")

    def order(self, qty):
        order = Order.objects.create(user=self.user, full_name="x", mobile="09121111111", total=self.product.price * qty)
        OrderItem.objects.create(order=order, product=self.product, product_name=self.product.name,
                                 unit_price=self.product.price, quantity=qty)
        Product.objects.filter(pk=self.product.pk).update(stock=F("stock") - qty)  # رزرو، مثل تسویه حساب سایت
        return order

    def test_delivery_creates_online_invoice_from_reserved_stock(self):
        order = self.order(3)
        order.status = Order.Status.DELIVERED
        order.save()
        invoice = SalesInvoice.objects.get(order=order)
        self.assertEqual((invoice.channel, invoice.status, invoice.total), ("online", "posted", 600_000))
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 7)
        self.assertEqual(inv.on_hand(self.product), 7)
        self.assertEqual(acc.account_balance(acc.COGS), 360_000)
        self.assertEqual(inv.reconcile_available(), [])
        # باشگاه مشتریان همچنان ژتون تحویل را می‌دهد
        self.assertTrue(PointTransaction.objects.filter(user=self.user, reason="order").exists())
        order.save()
        self.assertEqual(SalesInvoice.objects.filter(order=order).count(), 1)

    def test_delivery_fails_atomically_without_ledger_stock(self):
        order = self.order(3)
        Batch.objects.filter(product=self.product).update(status=Batch.Status.BLOCKED)
        order.status = Order.Status.DELIVERED
        with self.assertRaises(ValidationError):
            order.save()
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertFalse(SalesInvoice.objects.exists())

    def test_prescription_drug_cannot_be_sold_online(self):
        rx = drug("rx-online", requires_prescription=True)
        receive(rx, 2, 1, batch="R1")
        invoice = SalesInvoice.objects.create(channel="online", warehouse=inv.default_warehouse())
        sales.add_line(invoice, rx, 1)
        SalesPayment.objects.create(document=invoice, method=M.CASH, amount=invoice.total)
        with self.assertRaisesMessage(ValidationError, "آنلاین"):
            invoice.post(None)

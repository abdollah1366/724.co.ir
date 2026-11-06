"""بازبینی امنیت و روند (آبان ۱۴۰۵): قفل ورود، سقف فرم‌های عمومی، نشست کارکنان، تخفیف صندوق، مرجوعی نسیه،
ورود دوباره رسید، برگشت کالا به شرکت."""

import json

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounting import services as acc
from apps.audit.documents import DocStatus
from apps.inventory import requests
from apps.inventory import services as inv
from apps.inventory.models import InventoryDocument, StockMove, Warehouse
from apps.inventory.tests.fixtures import days, drug, main, open_session, pos_sale, receive, staff, supplier
from apps.purchasing.models import GoodsReceipt
from apps.sales.models import CashRegister, SalesInvoice, SalesPayment, SalesReturn


class LoginThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = staff("nurse1", "پرستار")

    @override_settings(LOGIN_FAILURE_LIMIT=3)
    def test_lock_after_failures_even_with_right_password(self):
        url = reverse("accounts:login")
        for _ in range(3):
            self.client.post(url, {"username": "nurse1", "password": "wrong-pass"})
        res = self.client.post(url, {"username": "nurse1", "password": "x-strong-pass-1"})
        self.assertContains(res, "موقتاً بسته شد")
        self.assertNotIn("_auth_user_id", self.client.session)
        cache.clear()
        self.client.post(url, {"username": "nurse1", "password": "x-strong-pass-1"})
        self.assertIn("_auth_user_id", self.client.session)

    @override_settings(LOGIN_FAILURE_LIMIT=2)
    def test_admin_login_is_throttled_too(self):
        url = reverse("admin:login")
        for _ in range(2):
            self.client.post(url, {"username": "nurse1", "password": "bad"})
        res = self.client.post(url, {"username": "nurse1", "password": "x-strong-pass-1"})
        self.assertContains(res, "موقتاً بسته شد")


class StaffIdleTests(TestCase):
    @override_settings(STAFF_IDLE_MINUTES=30)
    def test_staff_session_gets_idle_expiry(self):
        self.client.force_login(staff("ph", "داروساز مسئول فنی"))
        self.client.get(reverse("backoffice:dashboard"))
        self.assertEqual(self.client.session.get_expiry_age(), 30 * 60)


class PublicFormLimitTests(TestCase):
    def setUp(self):
        cache.clear()

    @override_settings(PUBLIC_FORM_LIMIT_PER_HOUR=2)
    def test_contact_form_is_capped_per_ip(self):
        from apps.core.models import ContactMessage

        data = {"name": "علی", "mobile": "09121234567", "subject": "سؤال", "message": "سلام"}
        for _ in range(4):
            self.client.post(reverse("core:contact"), data)
        self.assertLessEqual(ContactMessage.objects.count(), 2)


class PosDiscountTests(TestCase):
    def setUp(self):
        self.product = drug("amox", price=100_000, sku="AMOX")
        receive(self.product, 10, 50_000)
        # هر کاربر صندوق خودش را دارد (صندوق باز یک نفر را دیگری نمی‌تواند باز کند).
        CashRegister.objects.create(name="صندوق ۱", warehouse=main())
        CashRegister.objects.create(name="صندوق ۲", warehouse=main())

    def checkout(self, user, discount, register="صندوق ۱"):
        self.client.force_login(user)
        self.client.post(reverse("backoffice:pos"), {"register": CashRegister.objects.get(name=register).pk,
                                                     "opening_cash": 0})
        self.client.post(reverse("backoffice:pos_add"), {"code": "AMOX", "quantity": "1"})
        invoice = SalesInvoice.objects.filter(created_by=user, status=DocStatus.DRAFT).get()
        self.client.post(reverse("backoffice:pos_checkout"), {"token": invoice.client_token, "discount": discount,
                                                              "cash": invoice.subtotal + invoice.tax - discount, "card": 0, "credit": 0,
                                                              "insurance_amount": 0})
        invoice.refresh_from_db()
        return invoice

    def test_cashier_cannot_discount_pharmacist_can(self):
        cashier = staff("cash", "صندوق‌دار")
        self.assertEqual(self.checkout(cashier, 20_000).status, DocStatus.DRAFT)
        self.assertEqual(self.checkout(cashier, 0).status, DocStatus.POSTED)
        pharmacist = staff("ph", "داروساز مسئول فنی")
        invoice = self.checkout(pharmacist, 20_000, register="صندوق ۲")
        self.assertEqual((invoice.status, invoice.discount), (DocStatus.POSTED, 20_000))


class CreditReturnTests(TestCase):
    def test_credit_sale_cannot_be_refunded_in_cash(self):
        from apps.accounting.models import Party

        pharmacist = staff("ph", "داروساز مسئول فنی")
        product = drug("amox", price=100_000)
        receive(product, 10, 50_000)
        session = open_session(pharmacist)
        customer = Party.objects.create(kind=Party.Kind.CUSTOMER, name="مشتری نسیه")
        invoice = pos_sale(session, pharmacist, [(product, 2)], payments=[(SalesPayment.Method.CREDIT, None)],
                           customer=customer)
        self.client.force_login(pharmacist)
        line = invoice.lines.get()
        url = reverse("backoffice:sales_return", args=[invoice.pk])
        res = self.client.post(url, {f"q{line.pk}": "1", "reason": "تغییر نسخه", "refund_method": "cash"}, follow=True)
        self.assertContains(res, "نسیه بوده")
        self.assertFalse(SalesReturn.objects.filter(status=DocStatus.POSTED).exists())
        self.client.post(url, {f"q{line.pk}": "1", "reason": "تغییر نسخه", "refund_method": "credit"})
        self.assertEqual(SalesReturn.objects.get(status=DocStatus.POSTED).refund_amount, 100_000)

    def test_cash_refund_needs_open_shift_when_registers_exist(self):
        pharmacist = staff("ph", "داروساز مسئول فنی")
        product = drug("amox", price=100_000)
        receive(product, 10, 50_000)
        session = open_session(pharmacist)
        invoice = pos_sale(session, pharmacist, [(product, 1)])
        from apps.sales import services as sales

        sales.close_session(session, 100_000, pharmacist)
        self.client.force_login(pharmacist)
        line = invoice.lines.get()
        res = self.client.post(reverse("backoffice:sales_return", args=[invoice.pk]),
                               {f"q{line.pk}": "1", "reason": "x", "refund_method": "cash"}, follow=True)
        self.assertContains(res, "شیفت صندوق")


class ReceiveTwiceTests(TestCase):
    def test_same_form_token_creates_one_receipt(self):
        self.client.force_login(staff("keeper", "انباردار"))
        product = drug("amox")
        data = {"supplier": supplier().pk, "warehouse": main().pk, "doc_no": "F-1", "token": "tok-123",
                "items": json.dumps([{"product": product.pk, "quantity": 2, "unit_cost": 10, "batch": "B1",
                                      "expiry": days(400).isoformat(), "auth_code": "U1", "auth_ok": True}])}
        self.client.post(reverse("backoffice:receive"), data)
        res = self.client.post(reverse("backoffice:receive"), data, follow=True)
        self.assertContains(res, "قبلاً ثبت شده")
        self.assertEqual(GoodsReceipt.objects.count(), 1)
        data["items"] = data["items"].replace('"unit_cost": 10', '"unit_cost": -10')
        data["token"] = "tok-456"
        self.assertContains(self.client.post(reverse("backoffice:receive"), data, follow=True), "منفی")


class SupplierReturnTests(TestCase):
    def test_expired_stock_goes_back_to_supplier_and_reduces_debt(self):
        keeper = staff("keeper", "انباردار")
        sup = supplier()
        product = drug("amox")
        receive(product, 10, 50_000, sup=sup)
        qrn = Warehouse.objects.get(code="QRN")
        requests.build_document("transfer", main(), [{"product": product, "quantity": 4}], keeper, dest_warehouse=qrn)
        self.client.force_login(keeper)
        page = self.client.get(reverse("backoffice:warehouse") + f"?mode=sup_return&warehouse={qrn.pk}")
        self.assertContains(page, "برگشت کالا به شرکت")
        doc = requests.build_document("sup_return", qrn, [{"product": product, "quantity": 4}], keeper,
                                      reason="فراخوان", supplier=sup)
        self.assertEqual(doc.status, DocStatus.POSTED)
        self.assertEqual(inv.on_hand(product), 6)
        self.assertEqual(StockMove.objects.filter(move_type="sup_return").count(), 1)
        self.assertEqual(acc.account_balance(acc.INVENTORY), 300_000)
        # طلب از شرکت (بدهکار ۲۰۰ هزار در حساب پرداختنی) تا ثبت فاکتور
        self.assertEqual(acc.account_balance(acc.AP_SUPPLIERS), 200_000)
        doc.reverse(keeper, "اشتباه")
        self.assertEqual(inv.on_hand(product), 10)
        with self.assertRaisesMessage(Exception, "تأمین‌کننده"):
            requests.build_document("sup_return", qrn, [{"product": product, "quantity": 1}], keeper, reason="x")
        self.assertFalse(InventoryDocument.objects.filter(kind="sup_return", status=DocStatus.DRAFT, reason="x").exists())

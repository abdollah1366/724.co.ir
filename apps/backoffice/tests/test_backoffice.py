from io import StringIO

from django.contrib import admin
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.accounting import services as acc
from apps.backoffice import demo
from apps.inventory.models import InventoryDocument, InventoryDocumentLine
from apps.inventory.tests.fixtures import days, drug, receive, shelf, staff
from apps.sales.models import CashSession, SalesInvoice
from apps.shop.models import ProductPackage, Unit


class FullFlowTests(TestCase):
    """اثبات گردش خرید، انبار، فروش و حسابداری با اعداد دقیق (همان گردش دستور erp_demo)."""

    @classmethod
    def setUpTestData(cls):
        cls.result = demo.run()

    def test_profit_and_loss(self):
        pnl = self.result["pnl"]
        # فروش: ۱٬۵۰۰٬۰۰۰ + ۶۰۰٬۰۰۰ + ۳۰٬۰۰۰٬۰۰۰ + ۷۵۰٬۰۰۰ منهای مرجوعی ۳۰۰٬۰۰۰
        self.assertEqual(pnl["total_revenue"], 32_550_000)
        # بهای تمام‌شده: (۱۰ + ۵ - ۲) × ۱۰۴٬۰۰۰ + ۲ × ۲۰۰٬۰۰۰ + ۲۰٬۰۰۰٬۰۰۰
        self.assertEqual(pnl["cogs"], 21_752_000)
        self.assertEqual(pnl["gross_profit"], 10_798_000)
        # هزینه‌ها: اجاره ۱۰٬۰۰۰٬۰۰۰ + کسری صندوق ۱۰٬۰۰۰ - تخفیف خرید ۴۰۰٬۰۰۰
        self.assertEqual(pnl["net_profit"], 1_188_000)

    def test_books_balance_and_inventory_ledger_matches_general_ledger(self):
        totals = self.result["trial_totals"]
        self.assertEqual(totals["debit"], totals["credit"])
        self.assertEqual(self.result["inventory_gl"], 71_400_000 - 21_752_000)
        self.assertEqual(self.result["inventory_gl"], self.result["inventory_ledger"])
        self.assertEqual(self.result["availability_drift"], [])

    def test_receivables_payables_and_cash(self):
        self.assertEqual(self.result["supplier_payable"], 71_000_000 - 50_000_000)
        self.assertEqual(self.result["ar_insurers"], 420_000)
        self.assertEqual(self.result["ar_customers"], 33_000_000)
        self.assertEqual(self.result["expected_cash"], 5_000_000 + 1_500_000 + 180_000 - 300_000)
        self.assertEqual(self.result["session"].difference, -10_000)
        self.assertEqual(acc.account_balance(acc.VAT_OUTPUT), -3_000_000)

    def test_stock_by_batch_fefo_reservation_and_recall(self):
        p = self.result["products"]
        otc = p["otc"]
        balances = {(b.warehouse.code, b.batch.number): b.quantity for b in otc.balances.all()}
        # فروش ۱۰ و آنلاین ۵ هر دو از A2 (انقضای نزدیک‌تر)؛ ۲ مرجوعی باز شده در قرنطینه
        self.assertEqual(balances, {("MAIN", "A1"): 60, ("MAIN", "A2"): 25, ("QRN", "A2"): 2})
        self.assertEqual(otc.stock, 85)
        self.assertEqual(self.result["stock_after_reservation"], 85)
        self.assertEqual(p["insulin"].stock, 0)  # فراخوان
        self.assertEqual(p["device"].stock, 1)
        self.assertEqual(self.result["alert_counts"]["blocked"], 1)

    def test_prescription_sale_recorded_pharmacist(self):
        sale2 = self.result["docs"]["sales"][1]
        self.assertEqual(sale2.pharmacist_approved_by.username, "demo-pharmacist")
        self.assertEqual(self.result["docs"]["online_invoice"].channel, SalesInvoice.Channel.ONLINE)

    def test_demo_cannot_run_twice(self):
        with self.assertRaises(demo.DemoAlreadyRun):
            demo.run()


class DemoCommandTests(TestCase):
    def test_command_reports_success(self):
        out = StringIO()
        call_command("erp_demo", stdout=out)
        self.assertIn("همه کنترل‌ها درست است", out.getvalue())


class BackofficeViewTests(TestCase):
    def setUp(self):
        self.manager = staff("manager", "مدیر داروخانه")
        self.cashier = staff("cashier", "صندوق‌دار")
        self.pharmacist = staff("pharm", "داروساز مسئول فنی")
        self.product = drug("otc", price=150_000, sku="D-1")
        ProductPackage.objects.create(product=self.product, unit=Unit.objects.create(name="جعبه"), factor=10,
                                      barcode="6260000000028")
        receive(self.product, 50, 100_000, user=self.manager)

    def test_non_staff_redirected_and_finance_requires_permission(self):
        self.assertEqual(self.client.get(reverse("backoffice:dashboard")).status_code, 302)
        self.client.force_login(self.cashier)
        self.assertEqual(self.client.get(reverse("backoffice:dashboard")).status_code, 200)
        self.assertEqual(self.client.get(reverse("backoffice:pnl")).status_code, 403)
        self.client.force_login(self.manager)
        for name in ("dashboard", "alerts", "stock", "pnl", "trial_balance", "parties"):
            self.assertEqual(self.client.get(reverse(f"backoffice:{name}")).status_code, 200, name)
        response = self.client.get(reverse("backoffice:kardex", args=[self.product.pk]))
        self.assertContains(response, "رسید خرید")

    def test_pos_flow_with_barcode_and_double_submit_protection(self):
        self.client.force_login(self.cashier)
        register = demo.CashRegister.objects.create(name="صندوق ۱", warehouse=demo.Warehouse.objects.get(code="MAIN"))
        self.client.post(reverse("backoffice:pos"), {"register": register.pk, "opening_cash": 0})
        session = CashSession.objects.get(cashier=self.cashier, status="open")
        self.client.post(reverse("backoffice:pos_add"), {"code": "۶۲۶۰۰۰۰۰۰۰۰۲۸", "quantity": "1"})  # یک جعبه = ۱۰ ورق
        invoice = SalesInvoice.objects.get(session=session)
        self.assertEqual(invoice.lines.get().quantity, 10)
        data = {"token": invoice.client_token, "discount": 0, "cash": 1_500_000, "card": 0, "credit": 0,
                "insurance_amount": 0}
        response = self.client.post(reverse("backoffice:pos_checkout"), data)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "posted")
        self.assertRedirects(response, reverse("backoffice:pos_invoice", args=[invoice.pk]))
        self.client.post(reverse("backoffice:pos_checkout"), data)  # ارسال دوباره فرم
        self.assertEqual(SalesInvoice.objects.filter(status="posted").count(), 1)
        self.assertEqual(acc.account_balance(register.cash_code), 1_500_000)
        self.assertContains(self.client.get(reverse("backoffice:pos_invoice", args=[invoice.pk])), invoice.number)
        self.client.post(reverse("backoffice:pos_close"), {"counted_cash": 1_500_000})
        session.refresh_from_db()
        self.assertEqual((session.status, session.difference), ("closed", 0))

    def test_pos_rejects_unknown_code_and_requires_permission(self):
        accountant = staff("acc", "حسابدار")
        self.client.force_login(accountant)
        self.assertEqual(self.client.get(reverse("backoffice:pos")).status_code, 403)

    def test_api_lookup_and_stock(self):
        self.client.force_login(self.cashier)
        data = self.client.get(reverse("backoffice:api_lookup"), {"code": "6260000000028"}).json()
        self.assertEqual((data["id"], data["factor"], data["available"]), (self.product.pk, 10, 50))
        self.assertEqual(self.client.get(reverse("backoffice:api_lookup"), {"code": "x"}).status_code, 404)
        stock = self.client.get(reverse("backoffice:api_stock", args=[self.product.pk])).json()
        self.assertEqual(stock["balances"][0]["batch"], "B1")
        self.assertNotIn("finance", self.client.get(reverse("backoffice:api_dashboard")).json())
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse("backoffice:api_dashboard")).json()["finance"]["inventory_value"], 5_000_000)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("backoffice:api_lookup"), {"code": "D-1"}).status_code, 302)


class AdminTests(TestCase):
    def setUp(self):
        self.admin_user = staff("root", superuser=True)
        self.client.force_login(self.admin_user)

    def test_all_erp_admin_pages_load(self):
        for model in admin.site._registry:
            if model._meta.app_label in ("inventory", "purchasing", "sales", "accounting", "audit", "shop"):
                url = reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist")
                self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_post_and_reverse_from_admin(self):
        product = drug()
        doc = InventoryDocument.objects.create(kind="opening", warehouse=demo.Warehouse.objects.get(code="MAIN"))
        InventoryDocumentLine.objects.create(document=doc, product=product, batch_number="O1", expiry_date=days(300),
                                             location=shelf(), quantity=3, unit_cost=10)
        change = reverse("admin:inventory_inventorydocument_change", args=[doc.pk])
        self.assertContains(self.client.get(change), "ثبت نهایی سند")
        self.client.post(reverse("admin:inventory_inventorydocument_post", args=[doc.pk]))
        doc.refresh_from_db()
        self.assertEqual(doc.status, "posted")
        self.assertNotContains(self.client.get(change), "ثبت نهایی سند</button>")
        self.client.post(reverse("admin:inventory_inventorydocument_reverse", args=[doc.pk]), {"reason": "آزمون"})
        doc.refresh_from_db()
        self.assertEqual(doc.status, "reversed")

    def test_admin_without_permission_cannot_post(self):
        keeper = staff("cashier", "صندوق‌دار")
        self.client.force_login(keeper)
        doc = InventoryDocument.objects.create(kind="opening", warehouse=demo.Warehouse.objects.get(code="MAIN"))
        response = self.client.post(reverse("admin:inventory_inventorydocument_post", args=[doc.pk]))
        self.assertIn(response.status_code, (302, 403))
        doc.refresh_from_db()
        self.assertEqual(doc.status, "draft")

    def test_product_stock_not_editable_in_admin(self):
        product = drug(price=1000)
        url = reverse("admin:shop_product_change", args=[product.pk])
        self.assertNotContains(self.client.get(url), 'name="stock"')

    def test_order_cancel_or_delivery_only_via_actions(self):
        from apps.shop.models import Order

        order = Order.objects.create(user=self.admin_user, full_name="x", mobile="09120000000")
        url = reverse("admin:shop_order_change", args=[order.pk])
        data = {"full_name": "x", "mobile": "09120000000", "delivery_method": "pickup", "status": "cancelled",
                "items-TOTAL_FORMS": 0, "items-INITIAL_FORMS": 0}
        response = self.client.post(url, data)
        self.assertContains(response, "اکشن")
        order.refresh_from_db()
        self.assertEqual(order.status, "pending")

"""روند ساده‌شده: ورود کالا از شرکت در یک فرم، صفحه «خرید و بدهی شرکت‌ها»، مرجوعی از روی فاکتور و «کارهای من»."""

import datetime
import json

from django.test import TestCase
from django.urls import reverse

from apps.accounting import services as acc
from apps.accounting.models import Party, Payment
from apps.audit.documents import DocStatus, DocumentError
from apps.inventory import services as inv
from apps.inventory.models import StockBalance, Warehouse
from apps.inventory.tests.fixtures import cold_drug, days, drug, main, open_session, pos_sale, receive, shelf, staff, supplier
from apps.purchasing import quick
from apps.purchasing.models import GoodsReceipt, PurchaseInvoice, PurchaseOrder, PurchaseOrderLine
from apps.sales.models import SalesReturn
from apps.shop.models import ProductPackage, Unit


def row(product, qty, cost, batch="B1", expiry=None, auth="UID-1", ok=True, **kw):
    expiry = expiry if expiry is not None else days(400).strftime("%Y/%m/%d")
    return {"product": product.pk, "quantity": qty, "unit_cost": cost, "batch": batch, "expiry": expiry,
            "auth_code": auth, "auth_ok": ok, **kw}


class ParseExpiryTests(TestCase):
    def test_jalali_gregorian_and_month_only(self):
        self.assertEqual(quick.parse_expiry("۱۴۰۶/۰۵/۳۱"), datetime.date(2027, 8, 22))
        self.assertEqual(quick.parse_expiry("1406/12"), datetime.date(2028, 3, 19))  # پایان اسفند
        self.assertEqual(quick.parse_expiry("2027/02"), datetime.date(2027, 2, 28))
        self.assertEqual(quick.parse_expiry("2027-08-31"), datetime.date(2027, 8, 31))
        self.assertIsNone(quick.parse_expiry(""))
        with self.assertRaises(DocumentError):
            quick.parse_expiry("فردا")


class ReceiveScreenTests(TestCase):
    def setUp(self):
        self.supplier = supplier("پخش البرز")
        self.product = drug("amox", price=100_000, requires_authenticity_check=True)
        self.url = reverse("backoffice:receive")

    def post(self, rows, **fields):
        data = {"supplier": self.supplier.pk, "warehouse": main().pk, "doc_no": "F-100", "items": json.dumps(rows)}
        data.update(fields)
        return self.client.post(self.url, data, follow=True)

    def test_manager_receives_invoices_and_pays_in_one_step(self):
        manager = staff("mgr", "مدیر داروخانه")
        self.client.force_login(manager)
        self.assertContains(self.client.get(self.url), "ثبت ورود کالا")
        res = self.post([row(self.product, 20, 60_000)], with_invoice="1", tax="120000", pay_amount="500000",
                        pay_method="bank", pay_ref="TR-1")
        self.assertContains(res, "رسید انبار GR-")
        receipt = GoodsReceipt.objects.get()
        self.assertEqual(receipt.status, DocStatus.POSTED)
        self.assertEqual(receipt.lines.get().location, shelf())  # محل خودکار بر اساس شرایط نگهداری
        bill = PurchaseInvoice.objects.get()
        self.assertEqual((bill.status, bill.supplier_invoice_no, bill.total), (DocStatus.POSTED, "F-100", 1_320_000))
        self.assertEqual(inv.on_hand(self.product), 20)
        self.assertEqual(acc.account_balance(acc.GRNI), 0)
        self.assertEqual(quick.services.supplier_payable(self.supplier), 820_000)
        self.assertEqual(Payment.objects.get().status, DocStatus.POSTED)
        self.assertFalse(quick.receipts_without_invoice().exists())

    def test_storekeeper_receives_then_accountant_invoices_and_pays_from_hub(self):
        keeper = staff("keeper", "انباردار")
        self.client.force_login(keeper)
        page = self.client.get(self.url)
        self.assertNotContains(page, "with_invoice")  # انباردار فاکتور ثبت نمی‌کند
        self.post([row(self.product, 10, 50_000)], with_invoice="1")
        self.assertEqual(PurchaseInvoice.objects.count(), 0)
        receipt = GoodsReceipt.objects.get(status=DocStatus.POSTED)
        self.assertEqual(list(quick.receipts_without_invoice()), [receipt])

        accountant = staff("acc", "حسابدار")
        self.client.force_login(accountant)
        hub = self.client.get(reverse("backoffice:purchases"))
        self.assertContains(hub, receipt.number)
        self.assertContains(hub, 'value="F-100"')  # شماره فاکتوری که انباردار نوشته پیش‌فرض است
        self.client.post(reverse("backoffice:purchases"), {"action": "invoice", "receipt": receipt.pk,
                                                           "supplier_invoice_no": "F-100", "discount": "0", "tax": ""})
        self.assertEqual(PurchaseInvoice.objects.get().status, DocStatus.POSTED)
        self.assertFalse(quick.receipts_without_invoice().exists())
        self.client.post(reverse("backoffice:purchases"), {"action": "pay", "supplier": self.supplier.pk,
                                                           "amount": "۵۰۰٬۰۰۰", "method": "cash"})
        self.assertEqual(quick.services.supplier_payable(self.supplier), 0)

    def test_errors_keep_form_and_create_nothing(self):
        self.client.force_login(staff("mgr", "مدیر داروخانه"))
        res = self.post([row(self.product, 5, 10_000, ok=False)], new_supplier="شرکت تازه", supplier="")
        self.assertContains(res, "اصالت")
        self.assertContains(res, "داروی amox")  # ردیف‌ها پس از خطا در فرم می‌مانند
        self.assertFalse(GoodsReceipt.objects.exists())
        self.assertFalse(Party.objects.filter(name="شرکت تازه").exists())
        res = self.post([row(self.product, 5, 10_000, expiry=days(10).strftime("%Y/%m/%d"))])
        self.assertContains(res, "عمر باقی‌مانده")
        res = self.post([row(self.product, 5, 10_000, batch="")])
        self.assertContains(res, "شماره بچ")
        self.assertFalse(GoodsReceipt.objects.filter(status=DocStatus.POSTED).exists())

    def test_new_supplier_and_cold_chain_location(self):
        self.client.force_login(staff("keeper", "انباردار"))
        insulin = cold_drug()
        self.post([row(insulin, 3, 900_000, auth="UID-C1")], supplier="", new_supplier="پخش سرد", new_supplier_license="LIC-9")
        line = GoodsReceipt.objects.get().lines.get()
        self.assertEqual(line.location.storage_condition, insulin.storage_condition)
        self.assertEqual(GoodsReceipt.objects.get().supplier.license_no, "LIC-9")

    def test_receive_against_purchase_order(self):
        manager = staff("mgr", "مدیر داروخانه")
        po = PurchaseOrder.objects.create(supplier=self.supplier)
        po_line = PurchaseOrderLine.objects.create(document=po, product=self.product, quantity=10, unit_price=40_000)
        po.post(manager)
        self.client.force_login(manager)
        page = self.client.get(f"{self.url}?po={po.pk}")
        self.assertContains(page, "40000")
        self.assertIn(po, quick.open_orders())
        other = drug("other")
        res = self.post([row(other, 1, 1)], po=po.pk)
        self.assertContains(res, "در سفارش")
        self.post([row(self.product, 10, 40_000)], po=po.pk)
        self.assertEqual(po_line.received_quantity, 10)
        self.assertNotIn(po, quick.open_orders())

    def test_lookup_by_barcode_gs1_and_name(self):
        self.client.force_login(staff("keeper", "انباردار"))
        ProductPackage.objects.create(product=self.product, unit=Unit.objects.create(name="جعبه"), factor=10, barcode="6260000000123")
        receive(self.product, 1, 33_000, batch="OLD")
        url = reverse("backoffice:purchase_lookup")
        data = self.client.get(url, {"q": "(01)06260000000123(17)300131(10)LOT-9(21)SER1"}).json()["results"][0]
        self.assertEqual((data["id"], data["factor"], data["batch"], data["expiry"], data["serial"], data["last_cost"]),
                         (self.product.pk, 10, "LOT-9", "2030-01-31", "SER1", 33_000))
        self.assertEqual(self.client.get(url, {"q": "amox"}).json()["results"][0]["id"], self.product.pk)
        self.client.force_login(staff("cash", "صندوق‌دار"))
        self.assertEqual(self.client.get(url, {"q": "amox"}).status_code, 403)
        self.assertEqual(self.client.get(self.url).status_code, 403)


class ReturnScreenTests(TestCase):
    def setUp(self):
        self.pharmacist = staff("ph", "داروساز مسئول فنی")
        self.product = drug("amox", price=100_000)
        receive(self.product, 10, 50_000)
        session = open_session(self.pharmacist)
        self.invoice = pos_sale(session, self.pharmacist, [(self.product, 3)])
        self.client.force_login(self.pharmacist)

    def test_find_and_return_to_quarantine(self):
        res = self.client.get(reverse("backoffice:returns"), {"number": self.invoice.number[-5:]}, follow=True)
        self.assertContains(res, f"مرجوعی از فاکتور {self.invoice.number}")
        line = self.invoice.lines.get()
        res = self.client.post(reverse("backoffice:sales_return", args=[self.invoice.pk]),
                               {f"q{line.pk}": "2", "reason": "تغییر نسخه", "refund_method": "cash"}, follow=True)
        self.assertContains(res, "قرنطینه")
        ret = SalesReturn.objects.get()
        self.assertEqual((ret.status, ret.refund_amount), (DocStatus.POSTED, 200_000))
        qrn = StockBalance.objects.get(warehouse=Warehouse.objects.get(code="QRN"), product=self.product)
        self.assertEqual(qrn.quantity, 2)
        self.assertContains(self.client.get(reverse("backoffice:pos_invoice", args=[self.invoice.pk])), "مرجوعی از این فاکتور")
        res = self.client.post(reverse("backoffice:sales_return", args=[self.invoice.pk]),
                               {f"q{line.pk}": "2", "reason": "دوباره", "refund_method": "cash"}, follow=True)
        self.assertContains(res, "بیشتر از مانده")
        self.assertEqual(SalesReturn.objects.filter(status=DocStatus.POSTED).count(), 1)

    def test_cashier_cannot_post_returns(self):
        self.client.force_login(staff("cash", "صندوق‌دار"))
        self.assertEqual(self.client.get(reverse("backoffice:sales_return", args=[self.invoice.pk])).status_code, 403)


class TodoTests(TestCase):
    def test_dashboard_lists_my_pending_work(self):
        keeper = staff("keeper", "انباردار")
        receive(drug(), 1, 1_000, user=keeper)
        self.client.force_login(keeper)
        page = self.client.get(reverse("backoffice:dashboard"))
        self.assertContains(page, "کارهای من")
        self.assertContains(page, "ورود بار تازه شرکت پخش")
        self.assertNotContains(page, "منتظر ثبت فاکتور")
        self.assertContains(page, "ورود کالا از شرکت")  # منوی گروه‌بندی‌شده
        self.client.force_login(staff("acc", "حسابدار"))
        page = self.client.get(reverse("backoffice:dashboard"))
        self.assertContains(page, "رسید انبار منتظر ثبت فاکتور شرکت")
        self.assertContains(self.client.get(reverse("backoffice:purchases")), "بدهی به شرکت‌ها")

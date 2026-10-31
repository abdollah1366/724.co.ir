"""صفحه‌های اسکنی: صندوق سریع (fetch)، میز کار انبار، درخواست کالا، انبارگردانی و چاپ برچسب."""

import json

from django.test import TestCase
from django.urls import reverse

from apps.inventory import barcodes
from apps.inventory import services as inv
from apps.inventory.models import Batch, InventoryDocument, StockRequest, Warehouse
from apps.inventory.tests.fixtures import days, drug, main, receive, staff
from apps.sales.models import CashRegister, SalesInvoice
from apps.shop.models import ProductPackage, Unit

AJAX = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


def wh(code):
    return Warehouse.objects.get(code=code)


class FastPosTests(TestCase):
    def setUp(self):
        self.cashier = staff("cash", "صندوق‌دار")
        self.product = drug("amox", price=100_000)
        ProductPackage.objects.create(product=self.product, unit=Unit.objects.create(name="جعبه"), factor=10, barcode="6260000000123")
        receive(self.product, 30, 50_000, batch="LOT-1", expiry=days(400))
        CashRegister.objects.create(name="صندوق ۱", warehouse=main())
        self.client.force_login(self.cashier)
        self.client.post(reverse("backoffice:pos"), {"register": CashRegister.objects.get().pk, "opening_cash": 0})

    def test_scan_gs1_without_reload_then_change_quantity(self):
        page = self.client.get(reverse("backoffice:pos"))
        self.assertContains(page, "js/scanner.js")
        self.assertContains(page, 'data-scan="submit"')
        res = self.client.post(reverse("backoffice:pos_add"), {"code": "]d201062600000001231727010110LOT-1", "quantity": "1"}, **AJAX)
        data = res.json()
        self.assertTrue(data["ok"])
        self.assertIn("pos-checkout", data["html"])
        self.assertEqual(data["total"], 1_000_000)  # جعبه ۱۰ عددی
        line = SalesInvoice.objects.get().lines.get()
        res = self.client.post(reverse("backoffice:pos_quantity", args=[line.pk]), {"quantity": "3"}, **AJAX)
        self.assertEqual(res.json()["total"], 300_000)
        res = self.client.post(reverse("backoffice:pos_add"), {"code": "ناموجود"}, **AJAX)
        self.assertEqual(res.status_code, 400)
        self.assertIn("پیدا نشد", res.json()["message"])
        self.client.post(reverse("backoffice:pos_quantity", args=[line.pk]), {"quantity": "0"}, **AJAX)
        self.assertFalse(SalesInvoice.objects.get().lines.exists())


class WarehouseWorkbenchTests(TestCase):
    def setUp(self):
        self.keeper = staff("keeper", "انباردار")
        self.gauze = drug("gauze", price=10_000)
        receive(self.gauze, 20, 4_000, batch="G1", expiry=days(300))
        self.code = barcodes.ensure_barcode(self.gauze)
        self.client.force_login(self.keeper)

    def post(self, mode, items, **extra):
        data = {"mode": mode, "warehouse": main().pk, "items": json.dumps(items), "reason": "آزمون", **extra}
        return self.client.post(reverse("backoffice:warehouse"), data)

    def test_api_scan_and_transfer_to_ward(self):
        res = self.client.get(reverse("backoffice:api_scan"), {"code": self.code, "warehouse": main().pk})
        self.assertEqual((res.json()["product"]["id"], res.json()["on_hand"]), (self.gauze.pk, 20))
        self.assertContains(self.client.get(reverse("backoffice:warehouse")), "data-scan-list")
        self.post("transfer", [{"product": self.gauze.pk, "quantity": 5}], dest_warehouse=wh("DRESS").pk)
        self.assertEqual(inv.on_hand(self.gauze, wh("DRESS")), 5)

    def test_count_records_shortage_and_surplus_by_batch(self):
        batch = Batch.objects.get(product=self.gauze, number="G1")
        res = self.post("count", [{"product": self.gauze.pk, "batch": batch.pk, "quantity": 18}])
        self.assertContains(res, "سند کسری")
        self.assertEqual(inv.on_hand(self.gauze, main()), 18)
        self.post("count", [{"product": self.gauze.pk, "quantity": 21}])
        self.assertEqual(inv.on_hand(self.gauze, main()), 21)
        kinds = set(InventoryDocument.objects.values_list("kind", flat=True))
        self.assertTrue({"adjust_in", "adjust_out"} <= kinds)

    def test_ward_nurse_requests_and_keeper_issues(self):
        nurse = staff("nurse", "مسئول انبار بخش")
        dress = wh("DRESS")
        dress.keepers.add(nurse)
        self.client.force_login(nurse)
        self.client.post(reverse("backoffice:stock_requests"), {
            "requester": dress.pk, "items": json.dumps([{"product": self.gauze.pk, "quantity": 4}])})
        req = StockRequest.objects.get()
        self.assertEqual(req.source.code, "MAIN")
        # مسئول بخش نمی‌تواند از انبار دیگری مصرف بزند
        res = self.client.post(reverse("backoffice:warehouse"), {"mode": "consume", "warehouse": main().pk,
                                                                 "items": json.dumps([{"product": self.gauze.pk, "quantity": 1}])})
        self.assertEqual(res.status_code, 403)
        self.client.force_login(self.keeper)
        self.assertContains(self.client.get(reverse("backoffice:stock_requests")), req.number)
        self.client.post(reverse("backoffice:stock_request_action", args=[req.pk, "issue"]))
        self.assertEqual(inv.on_hand(self.gauze, dress), 4)
        self.client.force_login(nurse)
        self.client.post(reverse("backoffice:warehouse"), {"mode": "consume", "warehouse": dress.pk,
                                                          "items": json.dumps([{"product": self.gauze.pk, "quantity": 3}])})
        self.assertEqual(inv.on_hand(self.gauze, dress), 1)

    def test_labels_page_prints_product_and_batch_codes(self):
        batch = Batch.objects.get(product=self.gauze, number="G1")
        res = self.client.get(reverse("backoffice:labels"), {"q": self.gauze.name, "product": self.gauze.pk, "batch": batch.pk,
                                                             "copies": 3})
        self.assertContains(res, barcodes.batch_label(batch), count=3 * 2)  # متن زیر بارکد و برچسب aria
        self.assertContains(res, "<svg", count=3)
        receipt_labels = self.client.get(reverse("backoffice:labels"), {"receipt": self.gauze.moves.first().source_id})
        self.assertContains(receipt_labels, "class=\"label\"")

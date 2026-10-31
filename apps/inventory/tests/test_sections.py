"""بخش‌های انبار، درخواست کالا و تحویل FEFO، مصرف داخلی و بارکد."""

import datetime

from django.test import TestCase

from apps.accounting import services as acc
from apps.accounting.models import JournalLine
from apps.audit.documents import DocumentError
from apps.inventory import barcodes, requests
from apps.inventory import services as inv
from apps.inventory.models import InventoryDocument, StockBalance, StockRequest, Warehouse
from apps.shop.models import ProductPackage, Unit

from .fixtures import days, device, drug, main, receive, staff


def wh(code):
    return Warehouse.objects.get(code=code)


class SectionSetupTests(TestCase):
    def test_default_sections_exist_with_supply_chain(self):
        self.assertEqual(wh("MAIN").kind, Warehouse.Kind.CENTRAL)
        self.assertEqual(wh("QRN").kind, Warehouse.Kind.QUARANTINE)
        dress = wh("DRESS")
        self.assertEqual((dress.kind, dress.is_sellable, dress.supplies_from.code), ("ward", False, "MAIN"))
        self.assertEqual(wh("MAIN").supplies_from.code, "DEPOT")
        self.assertTrue(dress.locations.filter(storage_condition="cool").exists())


class StockRequestTests(TestCase):
    def setUp(self):
        self.keeper = staff("keeper", "انباردار")
        self.nurse = staff("nurse", "مسئول انبار بخش")
        self.gauze = drug("gauze", price=10_000)
        receive(self.gauze, 10, 4_000, batch="LATE", expiry=days(300))
        receive(self.gauze, 5, 4_000, batch="SOON", expiry=days(120))
        self.gauze.refresh_from_db()

    def test_issue_takes_nearest_expiry_and_moves_stock_out_of_sale(self):
        req = requests.create_request(wh("DRESS"), [(self.gauze, 8)], self.nurse)
        self.assertEqual((req.status, req.source.code), ("posted", "MAIN"))
        doc, short = requests.issue_request(req, self.keeper)
        self.assertEqual(short, {})
        self.assertEqual(doc.kind, InventoryDocument.Kind.TRANSFER)
        lines = sorted((line.batch_number, line.quantity) for line in doc.lines.all())
        self.assertEqual(lines, [("LATE", 3), ("SOON", 5)])
        req.refresh_from_db()
        self.assertEqual(req.progress, StockRequest.Progress.DONE)
        self.assertEqual(inv.on_hand(self.gauze, wh("DRESS")), 8)
        self.gauze.refresh_from_db()
        self.assertEqual(self.gauze.stock, 7)  # انبار بخش قابل فروش نیست

    def test_partial_issue_then_close(self):
        req = requests.create_request(wh("CLINIC"), [(self.gauze, 20)], self.nurse)
        _, short = requests.issue_request(req, self.keeper)
        self.assertEqual(short, {self.gauze: 5})
        req.refresh_from_db()
        self.assertEqual((req.progress, req.lines.get().issued_quantity), ("partial", 15))
        with self.assertRaisesMessage(DocumentError, "موجودی سالم"):
            requests.issue_request(req, self.keeper)
        requests.close_request(req, self.keeper)
        req.refresh_from_db()
        self.assertEqual(req.progress, "closed")

    def test_issued_request_cannot_be_cancelled_until_transfer_reversed(self):
        req = requests.create_request(wh("DRESS"), [(self.gauze, 2)], self.nurse)
        doc, _ = requests.issue_request(req, self.keeper)
        with self.assertRaises(DocumentError):
            req.reverse(self.keeper, "اشتباه")
        doc.reverse(self.keeper, "اشتباه")
        req.reverse(self.keeper, "اشتباه")
        self.assertEqual(inv.on_hand(self.gauze, wh("DRESS")), 0)

    def test_request_needs_supply_source_and_not_from_quarantine(self):
        Warehouse.objects.filter(code="PARA").update(supplies_from=None)
        with self.assertRaisesMessage(DocumentError, "انبار تأمین"):
            requests.create_request(wh("PARA"), [(self.gauze, 1)], self.nurse)
        with self.assertRaisesMessage(DocumentError, "قرنطینه"):
            requests.create_request(wh("DRESS"), [(self.gauze, 1)], self.nurse, source=wh("QRN"))


class ConsumptionTests(TestCase):
    def setUp(self):
        self.keeper = staff("keeper", "انباردار")
        self.syringe = drug("syringe", price=5_000)
        receive(self.syringe, 10, 2_000, batch="S1", expiry=days(200))
        requests.issue_request(requests.create_request(wh("DRESS"), [(self.syringe, 6)], self.keeper), self.keeper)

    def test_consumption_expenses_ward_cost_center(self):
        doc = requests.build_document(InventoryDocument.Kind.CONSUME, wh("DRESS"), [{"product": self.syringe, "quantity": 4}],
                                      self.keeper, reason="پانسمان بیماران امروز")
        self.assertEqual(doc.status, "posted")
        self.assertEqual(inv.on_hand(self.syringe, wh("DRESS")), 2)
        self.assertEqual(acc.account_balance(acc.CONSUMPTION), 8_000)
        line = JournalLine.objects.get(account__code=acc.CONSUMPTION)
        self.assertEqual(line.cost_center.code, "CLINIC")
        doc.reverse(self.keeper, "ثبت اشتباه")
        self.assertEqual(acc.account_balance(acc.CONSUMPTION), 0)
        self.assertEqual(inv.on_hand(self.syringe, wh("DRESS")), 6)

    def test_cannot_consume_more_than_ward_stock_or_expired_batch(self):
        with self.assertRaisesMessage(DocumentError, "کافی نیست"):
            requests.build_document(InventoryDocument.Kind.CONSUME, wh("DRESS"), [{"product": self.syringe, "quantity": 7}],
                                    self.keeper)
        self.assertFalse(InventoryDocument.objects.filter(kind="consume").exists())

    def test_expired_batch_can_be_wasted_but_not_consumed(self):
        old = drug("old", price=1_000)
        receive(old, 3, 500, batch="X", expiry=days(100))
        balance = StockBalance.objects.get(product=old)
        from apps.inventory.models import Batch

        Batch.objects.filter(pk=balance.batch_id).update(expiry_date=datetime.date(2020, 1, 1))
        with self.assertRaises(DocumentError):
            requests.build_document(InventoryDocument.Kind.CONSUME, main(), [{"product": old, "quantity": 1}], self.keeper)
        doc = requests.build_document(InventoryDocument.Kind.WASTE, main(), [{"product": old, "quantity": 3}], self.keeper,
                                      reason="انقضا")
        self.assertEqual(doc.status, "posted")


class BarcodeTests(TestCase):
    def setUp(self):
        self.amox = drug("amox", price=50_000)
        unit = Unit.objects.create(name="جعبه")
        ProductPackage.objects.create(product=self.amox, unit=unit, factor=10, barcode="6260000000123")

    def test_gs1_datamatrix_with_fnc1_and_brackets(self):
        raw = "]d20106260000000123" + "17270131" + "10LOT-77\x1d" + "21UID998877"
        parsed = barcodes.parse_gs1(raw)
        self.assertEqual(parsed["gtin"], "06260000000123")
        self.assertEqual(parsed["expiry"], datetime.date(2027, 1, 31))
        self.assertEqual((parsed["batch"], parsed["serial"]), ("LOT-77", "UID998877"))
        self.assertEqual(barcodes.parse_gs1("(01)06260000000123(17)280200(10)A1")["expiry"], datetime.date(2028, 2, 29))
        self.assertEqual(barcodes.parse_gs1("6260000000123"), {})

    def test_resolve_finds_package_factor_batch_and_expiry(self):
        receive(self.amox, 10, 30_000, batch="LOT-77", expiry=datetime.date(2027, 1, 31))
        scan = barcodes.resolve("(01)06260000000123(17)270131(10)LOT-77(21)S1")
        self.assertEqual((scan.product, scan.factor, scan.batch.number, scan.source), (self.amox, 10, "LOT-77", "gs1"))
        plain = barcodes.resolve("۶۲۶۰۰۰۰۰۰۰۱۲۳")
        self.assertEqual((plain.product, plain.factor), (self.amox, 10))
        label = barcodes.resolve(barcodes.batch_label(scan.batch))
        self.assertEqual((label.product, label.batch), (self.amox, scan.batch))
        self.assertFalse(barcodes.resolve("nothing-here").found)

    def test_internal_ean13_for_items_without_barcode(self):
        bandage = device("bandage", price=20_000)
        code = barcodes.ensure_barcode(bandage)
        self.assertTrue(code.startswith("200") and barcodes.is_valid_ean13(code))
        self.assertEqual(barcodes.ensure_barcode(bandage), code)
        self.assertEqual(barcodes.resolve(code).product, bandage)
        self.assertEqual(barcodes.ensure_barcode(self.amox), "6260000000123")

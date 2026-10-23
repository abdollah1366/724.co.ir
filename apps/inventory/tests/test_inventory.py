from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.accounting import services as acc
from apps.audit.documents import DocumentLocked
from apps.inventory import alerts, services
from apps.inventory.models import Batch, InventoryDocument, InventoryDocumentLine, Location, StockBalance, StockMove
from apps.shop.models import Order, OrderItem, Product

from .fixtures import days, device, drug, fridge, main, quarantine, receive, shelf, staff


class StockEngineTests(TestCase):
    def setUp(self):
        self.user = staff("keeper", "انباردار")
        self.product = drug()

    def test_receipt_updates_balances_available_stock_and_weighted_average(self):
        receive(self.product, 60, 100_000, batch="A1", expiry=days(400), user=self.user)
        receive(self.product, 40, 110_000, batch="A2", expiry=days(200), user=self.user)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 100)
        self.assertEqual(self.product.avg_cost, Decimal("104000.0000"))
        self.assertEqual(services.on_hand(self.product), 100)
        last = StockMove.objects.filter(product=self.product).order_by("id").last()
        self.assertEqual((last.product_qty_after, last.avg_cost_after), (100, Decimal("104000.0000")))

    def test_fefo_issues_nearest_expiry_first_at_average_cost(self):
        receive(self.product, 60, 100_000, batch="A1", expiry=days(400))
        receive(self.product, 40, 110_000, batch="A2", expiry=days(200))
        moves = services.stock_out(product=self.product, warehouse=main(), quantity=50, move_type="sale",
                                   source=("test", 1, ""), user=None, sellable_only=True)
        self.assertEqual([(m.batch.number, m.quantity) for m in moves], [("A2", -40), ("A1", -10)])
        self.assertEqual(sum(m.total_cost for m in moves), -50 * 104_000)

    def test_last_unit_takes_remaining_value_so_no_rounding_residue(self):
        receive(self.product, 3, 100_001)
        services.stock_out(product=self.product, warehouse=main(), quantity=1, move_type="sale", source=("t", 1, ""), user=None)
        services.stock_out(product=self.product, warehouse=main(), quantity=2, move_type="sale", source=("t", 2, ""), user=None)
        self.assertEqual(services.product_totals(self.product.pk), (0, 0))

    def test_insufficient_stock_is_rejected_and_nothing_changes(self):
        receive(self.product, 5, 100_000)
        with self.assertRaises(services.InsufficientStock):
            services.stock_out(product=self.product, warehouse=main(), quantity=6, move_type="sale",
                               source=("t", 1, ""), user=None)
        self.assertEqual(services.on_hand(self.product), 5)

    def test_database_rejects_negative_balance(self):
        receive(self.product, 1, 100_000)
        balance = StockBalance.objects.get(product=self.product)
        balance.quantity = -1
        with self.assertRaises(IntegrityError), transaction.atomic():
            balance.save()

    def test_expired_recalled_and_quarantined_stock_is_not_sellable(self):
        receive(self.product, 10, 100_000, batch="R1")
        batch = Batch.objects.get(product=self.product, number="R1")
        services.change_batch_status(batch, Batch.Status.RECALLED, "فراخوان سازمان غذا و دارو", self.user, "REF-1")
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 0)
        with self.assertRaises(services.InsufficientStock):
            services.stock_out(product=self.product, warehouse=main(), quantity=1, move_type="sale",
                               source=("t", 1, ""), user=None, sellable_only=True)
        self.assertEqual(len(alerts.blocked()), 1)
        services.change_batch_status(batch, Batch.Status.ACTIVE, "رفع فراخوان", self.user)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 10)
        self.assertEqual(batch.status_changes.count(), 2)

    def test_expire_batches_command_logic(self):
        receive(self.product, 4, 100_000, batch="E1")
        Batch.objects.filter(number="E1").update(expiry_date=days(-1))
        self.assertEqual(len(alerts.expired()), 1)
        self.assertEqual(services.expire_batches(), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 0)
        self.assertEqual(Batch.objects.get(number="E1").status, Batch.Status.EXPIRED)
        with self.assertRaisesMessage(ValidationError, "منقضی"):
            services.change_batch_status(Batch.objects.get(number="E1"), Batch.Status.ACTIVE, "آزادسازی", self.user)

    def test_storage_condition_must_match_location(self):
        insulin = drug("insulin", storage_condition="cool")
        with self.assertRaisesMessage(ValidationError, "یخچال"):
            receive(insulin, 1, 1_000_000, location=shelf())
        receive(insulin, 1, 1_000_000, location=fridge())
        self.assertEqual(services.on_hand(insulin), 1)

    def test_drug_requires_batch_and_future_expiry(self):
        with self.assertRaisesMessage(ValidationError, "شماره بچ"):
            receive(self.product, 1, 100, batch="")
        with self.assertRaisesMessage(ValidationError, "گذشته"):
            receive(self.product, 1, 100, expiry=days(-5))
        with self.assertRaisesMessage(ValidationError, "عمر باقی‌مانده"):
            receive(self.product, 1, 100, expiry=days(30))

    def test_same_batch_with_different_expiry_is_rejected(self):
        receive(self.product, 1, 100, batch="X", expiry=days(300))
        with self.assertRaisesMessage(ValidationError, "مغایرت"):
            receive(self.product, 1, 100, batch="X", expiry=days(301))

    def test_product_type_rules(self):
        product = Product(kind="drug", name="x", slug="x", track_batch=False)
        with self.assertRaises(ValidationError):
            product.clean()
        product = Product(kind="drug", name="x", slug="x", is_controlled=True)
        with self.assertRaises(ValidationError):
            product.clean()

    def test_available_stock_excludes_online_reservations(self):
        receive(self.product, 10, 100_000)
        user = staff("buyer")
        order = Order.objects.create(user=user, full_name="x", mobile="09120000000", total=0)
        OrderItem.objects.create(order=order, product=self.product, product_name="x", unit_price=1, quantity=3)
        Product.objects.filter(pk=self.product.pk).update(stock=7)  # همان کاری که تسویه حساب سایت می‌کند
        self.assertEqual(services.reconcile_available(), [])
        self.assertEqual(services.reserved_quantity(self.product), 3)
        with self.assertRaisesMessage(ValidationError, "رزرو"):
            services.stock_out(product=self.product, warehouse=main(), quantity=8, move_type="sale",
                               source=("t", 1, ""), user=None, sellable_only=True)

    def test_reconcile_detects_and_fixes_drift(self):
        receive(self.product, 10, 100_000)
        Product.objects.filter(pk=self.product.pk).update(stock=3)
        diffs = services.reconcile_available(fix=True)
        self.assertEqual([(d[1], d[2]) for d in diffs], [(3, 10)])
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 10)


class InventoryDocumentTests(TestCase):
    def setUp(self):
        self.user = staff("keeper", "انباردار")
        self.product = drug()

    def doc(self, kind, **kw):
        return InventoryDocument.objects.create(kind=kind, warehouse=main(), created_by=self.user, **kw)

    def test_opening_document_posts_stock_and_journal(self):
        doc = self.doc(InventoryDocument.Kind.OPENING)
        InventoryDocumentLine.objects.create(document=doc, product=self.product, batch_number="O1", expiry_date=days(300),
                                             location=shelf(), quantity=10, unit_cost=50_000)
        doc.post(self.user)
        self.assertTrue(doc.number.startswith("INV-"))
        self.assertEqual(services.on_hand(self.product), 10)
        self.assertEqual(acc.account_balance(acc.INVENTORY), 500_000)
        self.assertEqual(acc.account_balance(acc.OPENING_EQUITY), -500_000)

    def test_posted_document_and_lines_are_immutable(self):
        doc = self.doc(InventoryDocument.Kind.OPENING)
        line = InventoryDocumentLine.objects.create(document=doc, product=self.product, batch_number="O1",
                                                    expiry_date=days(300), location=shelf(), quantity=1, unit_cost=1)
        doc.post(self.user)
        doc.note = "تغییر"
        with self.assertRaises(DocumentLocked):
            doc.save()
        with self.assertRaises(DocumentLocked):
            doc.delete()
        line.quantity = 5
        with self.assertRaises(DocumentLocked):
            line.save()
        with self.assertRaises(DocumentLocked):
            line.delete()
        move = StockMove.objects.first()
        with self.assertRaises(PermissionError):
            move.save()
        with self.assertRaises(PermissionError):
            StockMove.objects.all().delete()
        with self.assertRaises(PermissionError):
            StockMove.objects.update(quantity=0)

    def test_document_cannot_be_posted_twice(self):
        doc = self.doc(InventoryDocument.Kind.OPENING)
        InventoryDocumentLine.objects.create(document=doc, product=self.product, batch_number="O1", expiry_date=days(300),
                                             location=shelf(), quantity=1, unit_cost=1)
        stale = InventoryDocument.objects.get(pk=doc.pk)
        doc.post(self.user)
        with self.assertRaisesMessage(ValidationError, "قبلاً"):
            stale.post(self.user)
        self.assertEqual(services.on_hand(self.product), 1)

    def test_waste_and_its_reversal(self):
        receive(self.product, 10, 100_000, batch="W1")
        doc = self.doc(InventoryDocument.Kind.WASTE, reason="شکستگی")
        InventoryDocumentLine.objects.create(document=doc, product=self.product, batch_number="W1", location=shelf(), quantity=2)
        doc.post(self.user)
        self.assertEqual(services.on_hand(self.product), 8)
        self.assertEqual(acc.account_balance(acc.INVENTORY_LOSS), 200_000)
        with self.assertRaisesMessage(ValidationError, "علت"):
            doc.reverse(self.user, "")
        doc.reverse(self.user, "ثبت اشتباه")
        self.assertEqual(doc.status, "reversed")
        self.assertEqual(services.on_hand(self.product), 10)
        self.assertEqual(acc.account_balance(acc.INVENTORY_LOSS), 0)
        self.assertEqual(acc.account_balance(acc.INVENTORY), 1_000_000)
        self.assertEqual(StockMove.objects.filter(is_reversal=True).count(), 1)

    def test_transfer_to_quarantine_keeps_value_and_reduces_sellable(self):
        receive(self.product, 10, 100_000, batch="T1")
        doc = self.doc(InventoryDocument.Kind.TRANSFER, dest_warehouse=quarantine(), reason="مشکوک به آسیب")
        InventoryDocumentLine.objects.create(document=doc, product=self.product, batch_number="T1", location=shelf(),
                                             dest_location=Location.objects.get(code="Q-GEN"), quantity=4)
        doc.post(self.user)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 6)
        self.assertEqual(services.on_hand(self.product), 10)
        self.assertEqual(services.product_totals(self.product.pk), (10, 1_000_000))
        self.assertEqual(len(alerts.non_sellable_stock()), 1)
        doc.reverse(self.user, "اشتباه")
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 10)
        self.assertEqual(services.on_hand(self.product, quarantine()), 0)

    def test_serial_tracked_equipment(self):
        monitor = device(track_serial=True)
        receive(monitor, 1, 20_000_000, batch="SN1")
        with self.assertRaisesMessage(ValidationError, "سریال"):
            receive(monitor, 1, 20_000_000, batch="SN1")
        with self.assertRaisesMessage(ValidationError, "یک عدد"):
            receive(monitor, 2, 20_000_000, batch="SN2")

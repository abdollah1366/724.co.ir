"""آزمون هم‌زمانی واقعی با دو اتصال جدا به پایگاه داده (فقط روی PostgreSQL؛ SQLite نوشتن‌ها را سریالی می‌کند)."""

import threading
from unittest import skipUnless

from django.core.exceptions import ValidationError
from django.db import connection, connections, transaction
from django.test import TransactionTestCase

from apps.accounting import services as acc
from apps.inventory import services as inv
from apps.inventory.tests.fixtures import drug, main, open_session, pos_sale, receive, staff
from apps.sales.models import SalesInvoice


def run_parallel(*funcs):
    barrier = threading.Barrier(len(funcs))
    results = [None] * len(funcs)

    def worker(i, func):
        try:
            barrier.wait()
            results[i] = ("ok", func())
        except Exception as exc:  # noqa: BLE001
            results[i] = ("error", exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker, args=(i, f)) for i, f in enumerate(funcs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


@skipUnless(connection.vendor == "postgresql", "نیازمند PostgreSQL")
class ConcurrencyTests(TransactionTestCase):
    serialized_rollback = True  # داده‌های مهاجرت (انبارها، حساب‌ها، نقش‌ها) پس از هر آزمون بازگردانده شود

    def setUp(self):
        self.user = staff("pharm", "داروساز مسئول فنی")
        self.product = drug("race", price=100_000)
        receive(self.product, 5, 50_000)
        self.session = open_session(self.user)

    def test_two_simultaneous_sales_cannot_oversell(self):
        a = pos_sale(self.session, self.user, [(self.product, 4)], post=False)
        b = pos_sale(self.session, self.user, [(self.product, 4)], post=False)
        results = run_parallel(lambda: a.post(self.user), lambda: b.post(self.user))
        outcomes = sorted(r[0] for r in results)
        self.assertEqual(outcomes, ["error", "ok"])
        self.assertTrue(any(isinstance(r[1], ValidationError) for r in results if r[0] == "error"))
        self.assertEqual(inv.on_hand(self.product), 1)
        self.assertEqual(SalesInvoice.objects.filter(status="posted").count(), 1)
        self.assertEqual(acc.account_balance(acc.INVENTORY), 50_000)

    def test_parallel_stock_issues_from_different_documents_cannot_oversell(self):
        # بدون شماره‌گذاری سند (که خودش ثبت هم‌نوع را سریالی می‌کند)، فقط قفل ردیف کالا و مانده جلوی فروش بیش از موجودی را می‌گیرد.
        def issue(n):
            def run():
                with transaction.atomic():
                    return inv.stock_out(product=self.product, warehouse=main(), quantity=4, move_type="sale",
                                         source=("test", n, ""), user=None, sellable_only=True)
            return run

        results = run_parallel(issue(1), issue(2))
        self.assertEqual(sorted(r[0] for r in results), ["error", "ok"])
        self.assertEqual(inv.on_hand(self.product), 1)
        self.assertEqual(inv.product_totals(self.product.pk), (1, 50_000))

    def test_same_document_posted_twice_in_parallel_posts_once(self):
        invoice = pos_sale(self.session, self.user, [(self.product, 1)], post=False)
        copy = SalesInvoice.objects.get(pk=invoice.pk)
        results = run_parallel(lambda: invoice.post(self.user), lambda: copy.post(self.user))
        self.assertEqual(sorted(r[0] for r in results), ["error", "ok"])
        self.assertEqual(inv.on_hand(self.product), 4)
        self.assertEqual(acc.JournalEntry.objects.filter(source_type="sales.salesinvoice").count(), 1)

import datetime

from django.conf import settings
from django.test import override_settings
from django.utils import timezone

from apps.accounting import services as acc
from apps.accounting.models import (Account, AccountingPeriod, BankReconciliation, CostCenter, JournalEntry, ManualJournal,
                                     ManualJournalLine)
from apps.audit.documents import DocStatus, DocumentError
from apps.finance.models import TenderMethod

from .base import FinanceTestCase


def account(code):
    return Account.objects.get(code=code)


class ManualJournalTests(FinanceTestCase):
    def journal(self, rows, kind=ManualJournal.Kind.GENERAL, date=None):
        journal = ManualJournal.objects.create(description="سند آزمون", kind=kind, created_by=self.accountant,
                                               date=date or timezone.localdate())
        for code, debit, credit, center in rows:
            ManualJournalLine.objects.create(document=journal, account=account(code), debit=debit, credit=credit,
                                             cost_center=CostCenter.objects.get(code=center) if center else None)
        return journal

    def test_balanced_journal_posts_with_cost_center_and_reverses(self):
        journal = self.journal([("6101", 500_000, 0, "ADMIN"), (acc.CASH, 0, 500_000, None)])
        journal.post(self.accountant)
        entry = self.entry_for(journal)
        self.assertEqual(entry.lines.get(account__code="6101").cost_center.code, "ADMIN")
        journal.reverse(self.manager, "اصلاح")
        self.assertEqual(acc.account_balance("6101"), 0)
        self.assertEqual(JournalEntry.objects.filter(source_type="accounting.manualjournal").count(), 2)

    def test_unbalanced_or_controlled_account_rejected(self):
        with self.assertRaises(DocumentError):
            self.journal([("6101", 500_000, 0, None), (acc.CASH, 0, 400_000, None)]).post(self.accountant)
        with self.assertRaises(DocumentError):
            self.journal([(acc.AR_PATIENTS, 100, 0, None), (acc.CASH, 0, 100, None)]).post(self.accountant)
        with self.assertRaises(DocumentError):
            self.journal([("6101", 100, 0, None)]).post(self.accountant)

    def test_opening_journal_may_use_controlled_accounts(self):
        journal = self.journal([(acc.AR_PATIENTS, 1_000, 0, None), (acc.OPENING_EQUITY, 0, 1_000, None)],
                               kind=ManualJournal.Kind.OPENING)
        journal.post(self.accountant)
        self.assertEqual(journal.status, DocStatus.POSTED)


class PeriodTests(FinanceTestCase):
    def setUp(self):
        super().setUp()
        today = timezone.localdate()
        self.period = AccountingPeriod.objects.create(name="دوره آزمون", start_date=today - datetime.timedelta(days=10),
                                                      end_date=today + datetime.timedelta(days=10), is_year_end=True)

    def test_closed_period_blocks_posting_until_reopened(self):
        invoice = self.invoice(lines=[{"description": "x", "unit_price": 2_000_000}])
        closing = acc.close_period(self.period, self.manager, "پایان سال")
        self.assertIsNotNone(closing)
        self.assertEqual(acc.account_balance("4201"), 0)  # درآمد بسته شد
        self.assertEqual(acc.account_balance(acc.RETAINED_EARNINGS), -2_000_000)  # سود انباشته (بستانکار)
        self.assertBalanced()
        with self.assertRaises(DocumentError):
            self.invoice()
        with self.assertRaises(DocumentError):
            invoice.reverse(self.manager, "x")
        with self.assertRaises(DocumentError):  # سند بستن صادر شده
            acc.reopen_period(self.period, self.manager, "اصلاح")

    def test_reopen_monthly_period_with_reason(self):
        AccountingPeriod.objects.filter(pk=self.period.pk).update(is_year_end=False)
        self.period.refresh_from_db()
        acc.close_period(self.period, self.manager)
        with self.assertRaises(DocumentError):
            acc.reopen_period(self.period, self.manager, "")
        acc.reopen_period(self.period, self.manager, "ثبت جاافتاده")
        self.invoice()

    def test_overlapping_periods_rejected(self):
        from django.core.exceptions import ValidationError

        other = AccountingPeriod(name="هم‌پوشان", start_date=self.period.start_date, end_date=self.period.end_date)
        with self.assertRaises(ValidationError):
            other.full_clean()

    def test_require_open_period_rule(self):
        AccountingPeriod.objects.all().delete()
        with override_settings(FINANCE_RULES={**settings.FINANCE_RULES, "require_open_period": True}):
            with self.assertRaises(DocumentError):
                self.invoice()
        self.invoice()


class ReportTests(FinanceTestCase):
    def test_center_pnl_ledger_journal_and_cash_flow(self):
        invoice = self.invoice(lines=[{"description": "x", "unit_price": 3_000_000}])
        self.pay(3_000_000, [invoice])
        centers = {row["code"]: row for row in acc.profit_and_loss_by_center()}
        self.assertEqual(centers["CLINIC"]["revenue"], 3_000_000)
        ledger = acc.account_ledger(account(acc.AR_PATIENTS))
        self.assertEqual((ledger["opening"], ledger["closing"], len(ledger["rows"])), (0, 0, 2))
        self.assertEqual(acc.journal_book().count(), 2)
        flow = acc.cash_flow()
        self.assertEqual((flow["inflow"], flow["outflow"]), (3_000_000, 0))
        self.assertEqual(acc.unbalanced_entries(), [])


class BankReconciliationTests(FinanceTestCase):
    def test_clear_lines_and_finalize_with_difference_reason(self):
        self.pay(1_000_000, method=TenderMethod.BANK, reference="T1")
        self.pay(400_000, method=TenderMethod.BANK, reference="T2")
        rec = BankReconciliation.objects.create(account=account(acc.BANK), statement_date=timezone.localdate(),
                                                statement_balance=1_000_000, created_by=self.accountant)
        summary = acc.reconciliation_summary(rec)
        self.assertEqual((summary["book_balance"], len(summary["uncleared"])), (1_400_000, 2))
        first = next(line for line in summary["uncleared"] if line.debit == 1_000_000)
        self.assertEqual(acc.mark_cleared(rec, [first.pk], "ST-1"), 1)
        self.assertEqual(acc.mark_cleared(rec, [first.pk]), 0)  # تکراری
        self.assertEqual(acc.reconciliation_summary(rec)["difference"], 0)
        acc.finalize_reconciliation(rec, self.manager)
        with self.assertRaises(DocumentError):
            acc.mark_cleared(rec, [line.pk for line in summary["uncleared"]])
        rec2 = BankReconciliation.objects.create(account=account(acc.BANK), statement_date=timezone.localdate(),
                                                 statement_balance=1_300_000, created_by=self.accountant)
        with self.assertRaises(DocumentError):
            acc.finalize_reconciliation(rec2, self.manager)  # اختلاف بدون توضیح
        acc.finalize_reconciliation(rec2, self.manager, "کارمزد بانک ثبت نشده")

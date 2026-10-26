import datetime
from decimal import Decimal

import jdatetime
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounting import services as acc
from apps.audit.documents import DocStatus, DocumentError
from apps.finance.models import Contract, ContractRule, ContractVersion, ProviderSettlement, SettlementLine
from apps.finance.services import contracts, settlements

from .base import PROVIDER_ID, FinanceTestCase

Basis = ContractRule.Basis
Y, M = 1405, 1
START, END = settlements.month_bounds(Y, M)


def day(n):
    return START + datetime.timedelta(days=n)


class SettlementBase(FinanceTestCase):
    def contract(self, rules=(), start=START, **version_fields):
        party = self.provider_party()
        contract = Contract.objects.create(kind=Contract.Kind.PROVIDER, title="قرارداد آزمون", party=party,
                                           provider_id=PROVIDER_ID, provider_name="دکتر آزمون", start_date=start,
                                           created_by=self.hr)
        self.version(contract, rules, start, **version_fields)
        contracts.set_status(contract, Contract.Status.ACTIVE, self.manager, "شروع")
        return contract

    def version(self, contract, rules, effective_from, **fields):
        version = contracts.new_version(contract, self.hr, effective_from=effective_from)
        version.rules.all().delete()
        for name, value in fields.items():
            setattr(version, name, value)
        version.save()
        for basis, value, *rest in rules:
            ContractRule.objects.create(version=version, basis=basis, service_code=rest[0] if rest else "",
                                        percent=Decimal(value) if basis != Basis.FIXED_PER_SERVICE else None,
                                        amount=value if basis == Basis.FIXED_PER_SERVICE else None)
        contracts.submit_version(version, self.hr)
        contracts.approve_version(version, self.manager)
        return version

    def settle(self, contract, year=Y, month=M, post=True):
        settlement = settlements.create_settlement(contract, year, month, self.accountant)
        if post:
            settlements.approve_finance(settlement, self.manager)
            settlements.approve_manager(settlement, self.approver)
            settlement.refresh_from_db()
            settlement.post(self.manager)
        return settlement

    def visit(self, price, date=None, **line):
        return self.invoice(date=date or day(3), lines=[{"description": "ویزیت", "service_code": line.pop("code", "VISIT"),
                                                         "unit_price": price, **line}],
                            discount_reason="آزمون" if line.get("discount") else "")


class CalculationTests(SettlementBase):
    def test_percent_of_billed_on_net_or_gross(self):
        net_contract = self.contract([(Basis.PERCENT_BILLED, "40")])
        self.visit(1_000_000, discount=200_000)
        settlement = self.settle(net_contract, post=False)
        self.assertEqual(settlement.total, 320_000)  # ۴۰٪ از ۸۰۰٬۰۰۰
        line = settlement.lines.get()
        self.assertEqual((line.kind, line.base_amount, line.detail["formula"]), ("billed", 800_000, "800,000 × 40.00٪"))

    def test_gross_share_base_and_fixed_per_service(self):
        contract = self.contract([(Basis.FIXED_PER_SERVICE, 150_000, "INJ"), (Basis.PERCENT_BILLED, "50")],
                                 share_base=ContractVersion.ShareBase.GROSS)
        self.visit(500_000, code="INJ", quantity=3)
        self.visit(1_000_000, discount=100_000)
        settlement = self.settle(contract, post=False)
        amounts = sorted(settlement.lines.values_list("amount", flat=True))
        self.assertEqual(amounts, [450_000, 500_000])  # ۳×۱۵۰٬۰۰۰ و ۵۰٪ ناخالص ۱٬۰۰۰٬۰۰۰

    def test_percent_of_collected_follows_payments_and_refunds(self):
        contract = self.contract([(Basis.PERCENT_COLLECTED, "30")])
        invoice = self.visit(1_000_000)
        self.pay(400_000, [invoice], date=day(5))
        first = self.settle(contract)
        self.assertEqual(first.total, 120_000)
        self.pay(600_000, [invoice], date=day(40))  # ماه بعد
        second = self.settle(contract, month=M + 1)
        self.assertEqual(second.total, 180_000)
        self.assertEqual(SettlementLine.objects.filter(kind="collected", is_void=False).count(), 2)

    def test_floor_and_cap(self):
        floor = self.contract([(Basis.PERCENT_BILLED, "10")], period_floor=500_000, period_cap=None)
        self.visit(1_000_000)
        settlement = self.settle(floor, post=False)
        self.assertEqual(settlement.total, 500_000)
        self.assertTrue(settlement.lines.filter(kind="floor_cap", amount=400_000).exists())

    def test_cap_reduces_total(self):
        contract = self.contract([(Basis.PERCENT_BILLED, "50")], period_cap=300_000)
        self.visit(1_000_000)
        self.assertEqual(self.settle(contract, post=False).total, 300_000)

    def test_fixed_monthly_prorated_for_partial_month(self):
        days_in_month = (END - START).days + 1
        contract = self.contract(fixed_monthly=31_000_000, start=day(days_in_month - 10))
        settlement = self.settle(contract, post=False)
        self.assertEqual(settlement.total, round(31_000_000 * 10 / days_in_month))

    def test_rule_by_version_effective_at_invoice_date(self):
        contract = self.contract([(Basis.PERCENT_BILLED, "40")])
        self.version(contract, [(Basis.PERCENT_BILLED, "60")], day(10))
        self.visit(1_000_000, date=day(2))
        self.visit(1_000_000, date=day(12))
        self.assertEqual(self.settle(contract, post=False).total, 1_000_000)

    def test_bonus_and_deduction_lines_survive_recalculation(self):
        contract = self.contract([(Basis.PERCENT_BILLED, "40")])
        self.visit(1_000_000)
        settlement = self.settle(contract, post=False)
        settlements.add_adjustment(settlement, -50_000, "کسر بابت تأخیر", self.accountant)
        settlement = settlements.calculate(settlement, self.accountant)
        self.assertEqual(settlement.total, 350_000)
        with self.assertRaises(DocumentError):
            settlements.add_adjustment(settlement, 10, "", self.accountant)


class LifecycleTests(SettlementBase):
    def test_four_eyes_post_and_ledger(self):
        contract = self.contract([(Basis.PERCENT_BILLED, "40")])
        self.visit(1_000_000)
        settlement = settlements.create_settlement(contract, Y, M, self.manager)
        with self.assertRaises(DocumentError):
            settlements.approve_finance(settlement, self.manager)  # ایجادکننده
        with self.assertRaises(DocumentError):
            settlement.post(self.manager)  # بدون تأیید
        settlement = ProviderSettlement.objects.get()
        ProviderSettlement.objects.filter(pk=settlement.pk).update(created_by=self.accountant)
        settlements.approve_finance(settlement, self.manager)
        with self.assertRaises(DocumentError):
            settlements.approve_manager(settlement, self.manager)  # همان تأییدکننده مالی
        settlements.approve_manager(settlement, self.approver)
        settlement.refresh_from_db()
        settlement.post(self.manager)
        lines = self.lines_of(self.entry_for(settlement))
        self.assertEqual(lines[settlements.EXPENSE_ACCOUNT], (400_000, 0))
        self.assertEqual(lines[acc.AP_PROVIDERS], (0, 400_000))
        self.assertEqual(self.entry_for(settlement).lines.get(account__code=acc.AP_PROVIDERS).party, contract.party)

    def test_no_double_settlement(self):
        contract = self.contract([(Basis.PERCENT_BILLED, "40")])
        self.visit(1_000_000)
        self.settle(contract)
        with self.assertRaises((IntegrityError, DocumentError)), transaction.atomic():
            settlements.create_settlement(contract, Y, M, self.accountant)
        self.assertEqual(self.settle(contract, month=M + 1, post=False).total, 0)
        # تلاش برای ثبت دوباره همان ردیف در پایگاه داده هم رد می‌شود
        line = SettlementLine.objects.filter(is_void=False).first()
        other = ProviderSettlement.objects.get(month=M + 1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            SettlementLine.objects.create(settlement=other, kind="billed", amount=1, description="x", dedupe_key=line.dedupe_key)

    def test_changed_source_data_blocks_posting(self):
        contract = self.contract([(Basis.PERCENT_BILLED, "40")])
        self.visit(1_000_000)
        settlement = self.settle(contract, post=False)
        settlements.approve_finance(settlement, self.manager)
        settlements.approve_manager(settlement, self.approver)
        self.visit(500_000, date=day(6))  # صورتحساب جدید در همان ماه پس از تأیید
        settlement.refresh_from_db()
        with self.assertRaises(DocumentError):
            settlement.post(self.manager)
        settlement = settlements.calculate(settlement, self.accountant)
        self.assertEqual((settlement.total, settlement.finance_approved_at), (600_000, None))

    def test_invoice_reversed_after_settlement_is_deducted_later(self):
        contract = self.contract([(Basis.PERCENT_BILLED, "40")])
        invoice = self.visit(1_000_000)
        self.settle(contract)
        invoice.reverse(self.manager, "ابطال پس از تسویه")
        j = jdatetime.date.fromgregorian(date=timezone.localdate())
        later = self.settle(contract, year=j.year, month=j.month, post=False)
        self.assertEqual(later.lines.get(kind="billed_rev").amount, -400_000)
        self.assertEqual(later.total, -400_000)

    def test_reversed_settlement_frees_lines_for_recalculation(self):
        contract = self.contract([(Basis.PERCENT_BILLED, "40")])
        self.visit(1_000_000)
        first = self.settle(contract)
        first.reverse(self.manager, "اشتباه در قرارداد")
        self.assertFalse(SettlementLine.objects.filter(settlement=first, is_void=False).exists())
        again = self.settle(contract)
        self.assertEqual((again.total, again.status), (400_000, DocStatus.POSTED))
        self.assertBalanced()


class ContractTests(SettlementBase):
    def test_version_approval_rules(self):
        party = self.provider_party()
        contract = Contract.objects.create(kind=Contract.Kind.PROVIDER, title="ق", party=party, provider_id=PROVIDER_ID,
                                           start_date=START, created_by=self.hr)
        with self.assertRaises(DocumentError):
            contracts.set_status(contract, Contract.Status.ACTIVE, self.manager, "بدون نسخه")
        version = contracts.new_version(contract, self.manager, effective_from=START)
        with self.assertRaises(DocumentError):
            contracts.submit_version(version, self.manager)  # بدون قاعده
        ContractRule.objects.create(version=version, basis=Basis.PERCENT_BILLED, percent=Decimal("10"))
        contracts.submit_version(version, self.manager)
        with self.assertRaises(DocumentError):
            contracts.approve_version(version, self.manager)  # تهیه‌کننده
        with self.assertRaises(DocumentError):
            contracts.approve_version(version, self.hr)  # مجوز ندارد
        contracts.reject_version(version, self.approver, "درصد اشتباه است")
        version.refresh_from_db()
        self.assertEqual(version.status, ContractVersion.Status.REJECTED)
        contracts.submit_version(version, self.manager)
        contracts.approve_version(version, self.approver)
        version.refresh_from_db()
        version.fixed_monthly = 1
        with self.assertRaises(PermissionError):
            version.save()  # نسخه تأییدشده قفل است
        with self.assertRaises(PermissionError):
            contract.delete()

    def test_tick_renews_or_expires(self):
        renew = self.contract([(Basis.PERCENT_BILLED, "10")])
        Contract.objects.filter(pk=renew.pk).update(end_date=END, auto_renew=True, renewal_months=12)
        stats = contracts.tick(END + datetime.timedelta(days=1))
        self.assertEqual(stats, {"renewed": 1, "expired": 0})
        renew.refresh_from_db()
        self.assertEqual(renew.end_date.year, END.year + 1)
        Contract.objects.filter(pk=renew.pk).update(auto_renew=False)
        contracts.tick(renew.end_date + datetime.timedelta(days=1))
        renew.refresh_from_db()
        self.assertEqual(renew.status, Contract.Status.EXPIRED)

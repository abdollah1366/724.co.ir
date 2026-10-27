from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.accounting import services as acc
from apps.accounting.models import CostCenter, Party
from apps.audit.documents import DocumentError
from apps.finance.models import EmployeeLoan, PayItemType, PayrollLine, PayrollProfile, ProfilePayItem, TaxBracket
from apps.finance.services import payroll, settlements

from .base import FinanceTestCase

Y, M = 1405, 1
START, END = settlements.month_bounds(Y, M)


class PayrollTests(FinanceTestCase):
    def setUp(self):
        super().setUp()
        self.employee_user = get_user_model().objects.create_user("emp1", password="x-strong-pass-1")
        party = Party.objects.create(kind=Party.Kind.EMPLOYEE, name="کارمند آزمون")
        self.profile = PayrollProfile.objects.create(party=party, user=self.employee_user, personnel_no="P1", job_title="منشی",
                                                     cost_center=CostCenter.objects.get(code="ADMIN"),
                                                     base_salary=30_000_000, hired_on=START, monthly_hours=176)
        ProfilePayItem.objects.create(profile=self.profile, item_type=PayItemType.objects.get(code="HOUSING"), amount=3_000_000)

    def run_month(self):
        return payroll.create_run(Y, M, self.hr)

    def activate(self, code, rate=None):
        PayItemType.objects.filter(code=code).update(is_active=True, rate=rate)

    def test_seeded_statutory_items_are_inactive_without_rates(self):
        for code in ("INS_EMP", "INS_ER", "TAX", "OVERTIME"):
            item = PayItemType.objects.get(code=code)
            self.assertEqual((item.is_active, item.rate), (False, None), code)

    def test_base_and_fixed_items_prorated_by_absence(self):
        run = self.run_month()
        line = run.lines.get()
        self.assertEqual((line.work_days, line.gross, line.net), (30, 33_000_000, 33_000_000))
        PayrollLine.objects.filter(pk=line.pk).update(absence_days=3, bonus=1_000_000, other_deduction=500_000)
        run = payroll.calculate_run(run, self.hr)
        line.refresh_from_db()
        self.assertEqual(line.gross, 27_000_000 + 2_700_000 + 1_000_000)
        self.assertEqual(line.net, line.gross - 500_000)

    def test_missing_rate_or_table_stops_with_clear_error(self):
        self.activate("INS_EMP")
        with self.assertRaisesMessage(DocumentError, "نرخ قلم"):
            self.run_month()
        self.activate("INS_EMP", Decimal("7"))
        self.activate("TAX")
        with self.assertRaisesMessage(DocumentError, "جدول مالیات"):
            self.run_month()

    def test_overtime_needs_rate_and_hours(self):
        run = self.run_month()
        PayrollLine.objects.filter(run=run).update(overtime_hours=10)
        with self.assertRaises(DocumentError):
            payroll.calculate_run(run, self.hr)
        self.activate("OVERTIME", Decimal("1.4"))
        payroll.calculate_run(run, self.hr)
        line = run.lines.get()
        hourly = Decimal(30_000_000) / 176
        self.assertEqual(line.gross - 33_000_000, int((hourly * 10 * Decimal("1.4")).quantize(Decimal(1), "ROUND_HALF_UP")))

    def test_configured_insurance_and_progressive_tax(self):
        self.activate("INS_EMP", Decimal("7"))
        self.activate("INS_ER", Decimal("20"))
        self.activate("TAX")
        TaxBracket.objects.create(year=Y, from_amount=0, to_amount=20_000_000, rate=Decimal("0"))
        TaxBracket.objects.create(year=Y, from_amount=20_000_000, to_amount=None, rate=Decimal("10"))
        run = self.run_month()
        line = run.lines.get()
        insurable = sum(i.amount for i in line.items.filter(item_type__insurable=True, item_type__kind="earning"))
        ins = round(insurable * 0.07)
        taxable_items = sum(i.amount for i in line.items.filter(item_type__taxable=True, item_type__kind="earning"))
        reduces = PayItemType.objects.get(code="INS_EMP").reduces_taxable
        taxable = taxable_items - (ins if reduces else 0)
        tax = round(max(taxable - 20_000_000, 0) * 0.10)
        self.assertEqual(line.employer_cost, round(insurable * 0.20))
        self.assertEqual(line.net, line.gross - ins - tax)

    def test_loan_installments_and_four_eyes_post(self):
        loan = EmployeeLoan.objects.create(profile=self.profile, amount=12_000_000, installment=5_000_000, method="bank",
                                           reference_no="L-1", created_by=self.hr)
        loan.post(self.manager)
        run = self.run_month()
        line = run.lines.get()
        self.assertEqual(line.net, 33_000_000 - 5_000_000)
        with self.assertRaises(DocumentError):
            payroll.approve_run(run, self.hr)  # مجوز تأیید ندارد
        with self.assertRaises(DocumentError):
            run.post(self.manager)  # تأیید نشده
        payroll.approve_run(run, self.manager)
        run.refresh_from_db()
        run.post(self.manager)
        lines = self.lines_of(self.entry_for(run))
        self.assertEqual(lines[acc.SALARIES_PAYABLE], (0, 28_000_000))
        self.assertEqual(lines[acc.EMPLOYEE_LOANS], (0, 5_000_000))
        self.assertEqual(payroll.loan_remaining(loan), 7_000_000)
        with self.assertRaises(DocumentError):
            loan.reverse(self.manager, "x")  # قسط کسر شده
        self.assertBalanced()

    def test_recalculation_after_approval_requires_new_approval(self):
        run = self.run_month()
        payroll.approve_run(run, self.manager)
        payroll.calculate_run(run, self.hr)
        run.refresh_from_db()
        self.assertIsNone(run.approved_at)

    def test_payslip_visible_to_owner_only(self):
        run = self.run_month()
        payroll.approve_run(run, self.manager)
        run.refresh_from_db()
        run.post(self.manager)
        line = run.lines.get()
        self.client.force_login(self.employee_user)
        self.assertEqual(self.client.get(reverse("finance:payslip", args=[line.pk])).status_code, 200)
        self.assertContains(self.client.get(reverse("finance:my_payslips")), reverse("finance:payslip", args=[line.pk]))
        stranger = get_user_model().objects.create_user("stranger", password="x-strong-pass-1")
        self.client.force_login(stranger)
        self.assertIn(self.client.get(reverse("finance:payslip", args=[line.pk])).status_code, (403, 404))
        self.client.force_login(self.cashier)
        self.assertIn(self.client.get(reverse("finance:payslip", args=[line.pk])).status_code, (403, 404))
        self.client.force_login(self.hr)
        self.assertEqual(self.client.get(reverse("finance:payslip", args=[line.pk])).status_code, 200)

"""داده و کاربران مشترک آزمون‌های مالی (همه ساختگی)."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.accounting import services as acc
from apps.accounting.models import JournalEntry, Party
from apps.core import flags
from apps.finance.models import TenderMethod
from apps.finance.services import billing
from apps.inventory.tests.fixtures import staff

PROVIDER_ID = 4242


class FinanceTestCase(TestCase):
    def setUp(self):
        flags.reset_cache()
        self.addCleanup(flags.reset_cache)
        self.cashier = staff("cashier", "صندوق‌دار")
        self.manager = staff("finmgr", "مدیر مالی")
        self.approver = staff("approver", "تأییدکننده پرداخت")
        self.accountant = staff("accountant", "حسابدار")
        self.hr = staff("hr", "مسئول منابع انسانی")
        self.patient = get_user_model().objects.create_user("patient1", password="x-strong-pass-1")

    # ---- کمک‌ها
    def invoice(self, *, lines=None, post=True, user=None, patient=None, provider_id=PROVIDER_ID, **kw):
        patient = patient or self.patient
        lines = lines or [{"description": "ویزیت", "service_code": "VISIT", "unit_price": 1_000_000}]
        return billing.create_invoice(patient_user_id=patient.pk, patient_name="بیمار آزمون", lines=lines,
                                      user=user or self.manager, provider_id=provider_id, provider_name="دکتر آزمون",
                                      post=post, **kw)

    def pay(self, amount, invoices=(), method=TenderMethod.CASH, reference="", key=None, patient=None, **kw):
        patient = patient or self.patient
        return billing.receive(patient_user_id=patient.pk, patient_name="بیمار آزمون", user=self.cashier,
                               invoices=list(invoices), idempotency_key=key,
                               tenders=[{"method": method, "amount": amount, "reference_no": reference}], **kw)

    def entry_for(self, doc, reversal=False):
        return JournalEntry.objects.get(source_type=doc._meta.label_lower, source_id=doc.pk, is_reversal=reversal)

    def lines_of(self, entry):
        """{کد حساب: (بدهکار، بستانکار)} جمع‌شده."""
        result = {}
        for line in entry.lines.select_related("account"):
            d, c = result.get(line.account.code, (0, 0))
            result[line.account.code] = (d + line.debit, c + line.credit)
        return result

    def assertBalanced(self):
        _, totals = acc.trial_balance()
        self.assertEqual(totals["debit"], totals["credit"])

    def provider_party(self):
        return Party.objects.create(kind=Party.Kind.PROVIDER, name="دکتر آزمون")


import json
from io import StringIO

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.urls import reverse

from apps.audit.roles import ROLES, role_permissions
from apps.core import flags
from apps.ehr.tests.test_ehr import appointment, doctor as make_doctor
from apps.finance.models import Contract, Invoice, ProviderSettlement, Receipt
from apps.finance.services import billing
from apps.inventory.tests.fixtures import staff

from .base import FinanceTestCase
from .test_settlements import Basis, SettlementBase

FINANCE_ROLES = ["مدیر مالی", "حسابدار", "صندوق‌دار", "تأییدکننده پرداخت", "مسئول منابع انسانی", "حسابرس"]


class RoleTests(FinanceTestCase):
    def test_finance_roles_have_no_clinical_permissions(self):
        for role in FINANCE_ROLES:
            perms = role_permissions(ROLES[role])
            clinical = [p for p in perms if p.startswith(("ehr.", "lab.labresult", "telemed.consultmessage"))]
            self.assertEqual(clinical, [], role)

    def test_auditor_is_read_only(self):
        perms = role_permissions(ROLES["حسابرس"])
        self.assertTrue(perms)
        self.assertTrue(all(p.split(".")[1].startswith("view_") for p in perms), perms)

    def test_cashier_cannot_discount_approve_or_reverse(self):
        perms = set(role_permissions(ROLES["صندوق‌دار"]))
        for forbidden in ("finance.give_invoice_discount", "finance.approve_refund", "finance.reverse_invoice",
                          "finance.view_payroll", "accounting.view_financial_reports"):
            self.assertNotIn(forbidden, perms)

    def test_groups_exist_after_migrate(self):
        self.assertEqual(Group.objects.filter(name__in=FINANCE_ROLES).count(), len(FINANCE_ROLES))


class CashierViewTests(FinanceTestCase):
    def setUp(self):
        super().setUp()
        self.appt = appointment(make_doctor())
        self.ehr_patient = self.appt.patient
        self.url = reverse("finance:cashier")

    def test_cashier_page_requires_permission(self):
        reception = staff("reception", "پذیرش درمانگاه")
        self.client.force_login(reception)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.force_login(self.cashier)
        page = self.client.get(f"{self.url}?patient={self.ehr_patient.pk}")
        self.assertContains(page, self.ehr_patient.full_name)
        self.assertNotContains(page, self.ehr_patient.national_code)  # فقط کد ملی ماسک‌شده

    def test_receive_with_double_submit_creates_one_receipt(self):
        invoice = billing.create_invoice(patient_id=self.ehr_patient.pk, patient_name="بیمار",
                                         lines=[{"description": "x", "unit_price": 500_000}], user=self.manager)
        self.client.force_login(self.cashier)
        data = {"patient": self.ehr_patient.pk, "cash": 500_000, "card": 0, "bank": 0, "advance": 0, "token": "tok-1",
                "invoices": [invoice.pk]}
        self.client.post(reverse("finance:cashier_receive"), data)
        self.client.post(reverse("finance:cashier_receive"), data)
        self.assertEqual(Receipt.objects.count(), 1)
        invoice.refresh_from_db()
        self.assertEqual(invoice.balance_due, 0)

    def test_cashier_cannot_allocate_to_other_patients_invoice(self):
        other = billing.create_invoice(patient_user_id=self.patient.pk, patient_name="دیگری",
                                       lines=[{"description": "x", "unit_price": 500_000}], user=self.manager)
        self.client.force_login(self.cashier)
        self.client.post(reverse("finance:cashier_receive"), {"patient": self.ehr_patient.pk, "cash": 500_000, "card": 0,
                                                              "bank": 0, "advance": 0, "token": "tok-2",
                                                              "invoices": [other.pk]})
        other.refresh_from_db()
        self.assertEqual(other.paid_amount, 0)
        self.assertEqual(Receipt.objects.get().advance, 500_000)

    def test_cashier_without_discount_permission_cannot_discount(self):
        doc = make_doctor(slug="dr-fee", council="99999")
        doc.visit_fee = 1_000_000
        doc.save()
        self.client.force_login(self.cashier)
        self.client.post(reverse("finance:cashier_invoice"), {"patient": self.ehr_patient.pk, "doctor": doc.pk, "visit": "on",
                                                              "quantity": 1, "discount": 100_000, "discount_reason": "x",
                                                              "token": "tok-3"})
        self.assertFalse(Invoice.objects.exists())
        self.client.post(reverse("finance:cashier_invoice"), {"patient": self.ehr_patient.pk, "doctor": doc.pk, "visit": "on",
                                                              "quantity": 1, "discount": 0, "token": "tok-4"})
        self.assertEqual(Invoice.objects.get().gross, 1_000_000)  # مبلغ از تعرفه سمت سرور

    def test_disabled_module_hides_pages_without_deleting_data(self):
        self.invoice()
        flags.set_flag("finance.billing", False, self.manager, "آزمون")
        self.client.force_login(self.cashier)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(Invoice.objects.count(), 1)


class ProviderPrivacyTests(SettlementBase):
    def test_doctor_sees_only_own_posted_settlement(self):
        doctor_user = staff("own-doc", "پزشک")
        doc = make_doctor(user=doctor_user)
        contract = self.contract([(Basis.PERCENT_BILLED, "40")])
        Contract.objects.filter(pk=contract.pk).update(provider_id=doc.pk)
        contract.refresh_from_db()
        self.invoice(provider_id=doc.pk, date=self.contract_day())
        settlement = self.settle(contract)
        self.client.force_login(doctor_user)
        self.assertEqual(self.client.get(reverse("finance:my_settlement", args=[settlement.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("finance:my_earnings")).status_code, 200)
        api = self.client.get(reverse("finance:api_my_settlements")).json()
        self.assertEqual([r["number"] for r in api["results"]], [settlement.number])
        other_user = staff("other-doc", "پزشک")
        make_doctor(user=other_user, slug="dr-other", council="55555")
        self.client.force_login(other_user)
        self.assertEqual(self.client.get(reverse("finance:my_settlement", args=[settlement.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("finance:api_my_settlements")).json()["results"], [])

    def contract_day(self):
        from .test_settlements import day

        return day(3)


class ApiTests(FinanceTestCase):
    def test_errors_are_json_without_traceback(self):
        response = self.client.get(reverse("finance:api_invoices"))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "unauthenticated")
        self.client.force_login(staff("nobody-staff"))
        self.assertEqual(self.client.get(reverse("finance:api_invoices")).status_code, 403)
        self.client.force_login(self.manager)
        bad = self.client.get(reverse("finance:api_invoices") + "?patient_id=abc")
        self.assertEqual(bad.status_code, 400)
        self.assertNotIn("Traceback", bad.content.decode())

    def test_invoice_list_is_paginated_and_versioned(self):
        for _ in range(3):
            self.invoice()
        self.client.force_login(self.manager)
        data = self.client.get(reverse("finance:api_invoices") + "?page_size=2").json()
        self.assertEqual((data["version"], data["count"], data["pages"], len(data["results"])), ("v1", 3, 2, 2))
        self.assertEqual(data["results"][0]["currency"], "IRR")
        number = data["results"][0]["number"]
        detail = self.client.get(reverse("finance:api_invoice", args=[number])).json()
        self.assertEqual(len(detail["invoice"]["lines"]), 1)

    def test_receipt_api_needs_idempotency_key_and_is_idempotent(self):
        patient = appointment(make_doctor()).patient
        invoice = billing.create_invoice(patient_id=patient.pk, patient_name="بیمار",
                                         lines=[{"description": "x", "unit_price": 300_000}], user=self.manager)
        self.client.force_login(self.cashier)
        url = reverse("finance:api_receipts")
        body = json.dumps({"patient_id": patient.pk, "tenders": [{"method": "cash", "amount": 300_000}],
                           "invoices": [invoice.number]})
        self.assertEqual(self.client.post(url, body, content_type="application/json").status_code, 400)
        first = self.client.post(url, body, content_type="application/json", HTTP_IDEMPOTENCY_KEY="key-00000001")
        second = self.client.post(url, body, content_type="application/json", HTTP_IDEMPOTENCY_KEY="key-00000001")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.json()["receipt"]["number"], second.json()["receipt"]["number"])
        self.assertEqual(Receipt.objects.count(), 1)
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_health_public_vs_staff(self):
        public = self.client.get(reverse("finance:health")).json()
        self.assertEqual(set(public), {"status", "database"})
        self.client.force_login(self.manager)
        detail = self.client.get(reverse("finance:api_health")).json()
        self.assertIn("events", detail)
        self.assertIn("finance.billing", detail["flags"])


class DemoCommandTests(FinanceTestCase):
    def test_demo_runs_clean_and_only_once(self):
        out = StringIO()
        call_command("finance_demo", stdout=out)
        self.assertIn("بدون مغایرت", out.getvalue())
        self.assertEqual(ProviderSettlement.objects.count(), 1)
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("finance_demo", stdout=StringIO())

    def test_reconcile_command_exit_status(self):
        out = StringIO()
        call_command("finance_reconcile", stdout=out)
        self.assertIn("✓", out.getvalue())

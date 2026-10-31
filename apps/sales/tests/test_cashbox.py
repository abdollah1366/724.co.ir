"""صندوق‌های بخش‌ها: تعریف توسط مدیر، حساب نقد اختصاصی، شیفت و اتصال صندوق درمانگاه و آزمایشگاه."""

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.accounting import services as acc
from apps.audit.documents import DocumentError
from apps.finance.models import TenderMethod
from apps.finance.services import billing
from apps.finance.tests.base import FinanceTestCase
from apps.inventory.tests.fixtures import drug, main, pos_sale, receive, staff
from apps.lab.services import orders as lab_orders
from apps.lab.tests.fixtures import make_order, patient, seed
from apps.sales import cashbox
from apps.sales import services as sales
from apps.sales.models import CashRegister

D = CashRegister.Department


class CashRegisterDefinitionTests(TestCase):
    def test_each_new_register_gets_its_own_cash_account(self):
        a = CashRegister.objects.create(name="صندوق داروخانه ۱", warehouse=main())
        b = CashRegister.objects.create(name="صندوق درمانگاه", department=D.CLINIC)
        self.assertNotEqual(a.cash_account, b.cash_account)
        self.assertTrue(a.cash_account.code.startswith(acc.CASH) and a.cash_account.is_cash)
        self.assertEqual((a.cash_account.code, b.cash_account.code), ("110101", "110102"))

    def test_pharmacy_register_needs_a_warehouse(self):
        with self.assertRaises(ValidationError):
            CashRegister(name="بی‌انبار", department=D.PHARMACY).full_clean()
        CashRegister(name="آزمایشگاه", department=D.LAB).full_clean()

    def test_only_listed_cashiers_and_one_open_shift_per_user(self):
        alice, bob = staff("alice", "صندوق‌دار"), staff("bob", "صندوق‌دار")
        clinic = CashRegister.objects.create(name="درمانگاه", department=D.CLINIC)
        lab = CashRegister.objects.create(name="آزمایشگاه", department=D.LAB)
        clinic.cashiers.add(alice)
        with self.assertRaisesMessage(DocumentError, "مجاز"):
            sales.open_session(clinic, bob)
        sales.open_session(clinic, alice)
        with self.assertRaisesMessage(DocumentError, "شیفت باز"):
            sales.open_session(lab, alice)
        carol = staff("carol", "صندوق‌دار")
        clinic.cashiers.add(carol)
        with self.assertRaisesMessage(DocumentError, "شیفت باز"):
            sales.open_session(clinic, carol)
        self.assertEqual([r.name for r in cashbox.registers_for(bob)], ["آزمایشگاه"])

    def test_cannot_sell_goods_from_non_pharmacy_register(self):
        user = staff("ph", "داروساز مسئول فنی")
        product = drug("x", price=10_000)
        receive(product, 5, 5_000)
        cafe = CashRegister.objects.create(name="کافی‌شاپ", department=D.CAFE, warehouse=main())
        session = sales.open_session(cafe, user)
        with self.assertRaisesMessage(DocumentError, "داروخانه"):
            pos_sale(session, user, [(product, 1)])


class PharmacyRegisterLedgerTests(TestCase):
    def setUp(self):
        self.user = staff("ph", "داروساز مسئول فنی")
        self.product = drug("x", price=100_000)
        receive(self.product, 10, 50_000)
        self.a = CashRegister.objects.create(name="صندوق ۱", warehouse=main())
        self.b = CashRegister.objects.create(name="صندوق ۲", warehouse=main())

    def test_each_register_keeps_its_own_cash_and_shortage(self):
        other = staff("ph2", "داروساز مسئول فنی")
        s1 = sales.open_session(self.a, self.user, 200_000)
        s2 = sales.open_session(self.b, other, 0)
        pos_sale(s1, self.user, [(self.product, 3)])
        pos_sale(s2, other, [(self.product, 1)])
        self.assertEqual(sales.session_expected_cash(s1), 500_000)
        self.assertEqual(sales.session_expected_cash(s2), 100_000)
        self.assertEqual(acc.account_balance(self.a.cash_code), 300_000)
        closed = sales.close_session(s1, 490_000, self.user)
        self.assertEqual((closed.expected_cash, closed.difference), (500_000, -10_000))
        self.assertEqual(acc.account_balance(self.a.cash_code), 290_000)
        self.assertEqual(acc.account_balance(acc.CASH_OVER_SHORT), 10_000)
        self.assertEqual(acc.account_balance(self.b.cash_code), 100_000)

    def test_next_shift_starts_from_its_own_opening_cash(self):
        s1 = sales.open_session(self.a, self.user, 0)
        pos_sale(s1, self.user, [(self.product, 2)])
        sales.close_session(s1, 200_000, self.user)
        s2 = sales.open_session(self.a, self.user, 50_000)
        pos_sale(s2, self.user, [(self.product, 1)])
        self.assertEqual(sales.session_expected_cash(s2), 150_000)

    def test_card_goes_to_register_terminal_account(self):
        terminal = cashbox.create_cash_account("کارتخوان ملت")
        self.a.card_account = terminal
        self.a.save()
        session = sales.open_session(self.a, self.user)
        pos_sale(session, self.user, [(self.product, 1)], payments=[("card", None)], )
        self.assertEqual(acc.account_balance(terminal.code), 100_000)
        self.assertEqual(acc.account_balance(acc.BANK), 0)


class ClinicCashboxTests(FinanceTestCase):
    def test_without_clinic_register_old_behaviour_stays(self):
        receipt = self.pay(1_000)
        self.assertEqual(self.lines_of(self.entry_for(receipt))[acc.CASH], (1_000, 0))

    def test_clinic_register_requires_open_shift_and_receives_into_it(self):
        register = CashRegister.objects.create(name="صندوق پذیرش درمانگاه", department=D.CLINIC)
        with self.assertRaisesMessage(DocumentError, "شیفت صندوق"):
            self.pay(1_000)
        session = sales.open_session(register, self.cashier, 100_000)
        invoice = self.invoice(lines=[{"description": "x", "unit_price": 700_000}])
        receipt = billing.receive(patient_user_id=self.patient.pk, patient_name="بیمار", user=self.cashier, invoices=[invoice],
                                  tenders=[{"method": TenderMethod.CASH, "amount": 500_000},
                                           {"method": TenderMethod.CARD, "amount": 200_000, "reference_no": "P1"}])
        lines = self.lines_of(self.entry_for(receipt))
        self.assertEqual(lines[register.cash_code], (500_000, 0))
        self.assertEqual(lines[acc.BANK], (200_000, 0))
        self.assertEqual(receipt.tenders.get(method="cash").money_account, register.cash_account)
        self.assertEqual(sales.session_expected_cash(session), 600_000)
        closed = sales.close_session(session, 600_000, self.cashier)
        self.assertEqual(closed.difference, 0)

    def test_general_register_serves_clinic_and_lab_but_not_pharmacy(self):
        general = CashRegister.objects.create(name="صندوق مشترک", department=D.GENERAL)
        self.assertTrue(cashbox.serves(general, D.CLINIC) and cashbox.serves(general, D.LAB))
        self.assertFalse(cashbox.serves(general, D.PHARMACY))
        sales.open_session(general, self.cashier)
        receipt = self.pay(5_000)
        self.assertEqual(self.lines_of(self.entry_for(receipt))[general.cash_code], (5_000, 0))


class LabCashboxTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed()

    def test_lab_payment_goes_to_lab_register(self):
        user = staff("rec", "پذیرش آزمایشگاه")
        order = make_order(user, patient(), ["FBS"])
        register = CashRegister.objects.create(name="صندوق آزمایشگاه", department=D.LAB)
        with self.assertRaisesMessage(DocumentError, "شیفت صندوق"):
            lab_orders.receive(order, user, 1_000)
        session = sales.open_session(register, user)
        payment = lab_orders.receive(order, user, 1_000)
        self.assertEqual(payment.cash_session, session)
        self.assertEqual(acc.account_balance(register.cash_code), 1_000)
        self.assertEqual(sales.session_expected_cash(session), 1_000)


class CashboxPagesTests(TestCase):
    def test_manager_sees_registers_and_cashier_opens_and_closes_shift(self):
        manager = staff("boss", "مدیر کل")
        register = CashRegister.objects.create(name="صندوق درمانگاه", department=D.CLINIC)
        self.client.force_login(manager)
        page = self.client.get(reverse("backoffice:cashboxes"))
        self.assertContains(page, "صندوق درمانگاه")
        self.assertContains(page, "۱۱۰۱۰۱")
        cashier = staff("cash", "صندوق‌دار")
        self.client.force_login(cashier)
        self.assertEqual(self.client.get(reverse("backoffice:cashboxes")).status_code, 403)
        self.client.post(reverse("backoffice:my_cashbox"), {"register": register.pk, "opening_cash": 5000})
        session = cashbox.user_session(cashier)
        self.assertEqual(session.register, register)
        self.assertContains(self.client.get(reverse("backoffice:my_cashbox")), "نقد مورد انتظار")
        self.client.post(reverse("backoffice:my_cashbox_close"), {"counted_cash": 5000})
        session.refresh_from_db()
        self.assertEqual((session.status, session.difference), ("closed", 0))

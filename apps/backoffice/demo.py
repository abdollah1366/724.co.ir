"""گردش کامل نمونه: خرید ← رسید ← فاکتور ← پرداخت ← فروش حضوری/بیمه‌ای/نسیه/آنلاین ← مرجوعی ← هزینه ← بستن صندوق ← فراخوان.

با `python manage.py erp_demo` اجرا می‌شود و همین تابع در آزمون خودکار با مقادیر دقیق بررسی می‌شود.
همه داده‌ها با پسوند «(نمونه)» ساخته می‌شوند تا با داده واقعی اشتباه نشوند.
"""

import datetime

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db.models import Sum
from django.utils import timezone

from apps.accounting import services as acc
from apps.accounting.models import Account, Expense, Party, Payment, PaymentMethod
from apps.inventory import alerts
from apps.inventory import services as inv
from apps.inventory.models import Batch, Location, StockMove, Warehouse
from apps.purchasing.models import GoodsReceipt, GoodsReceiptLine, PurchaseInvoice, PurchaseOrder, PurchaseOrderLine
from apps.purchasing.services import fill_invoice_from_receipt, supplier_payable
from apps.sales import services as sales
from apps.sales.models import CashRegister, SalesInvoice, SalesPayment, SalesReturn, SalesReturnLine
from apps.shop.models import Order, Product, ProductKind, ProductPackage, StorageCondition, Unit

DEMO_SUPPLIER_CODE = "DEMO-SUP"


class DemoAlreadyRun(Exception):
    pass


def _user(username, first_name, groups=(), staff=True, password=None):
    User = get_user_model()
    user, _ = User.objects.get_or_create(username=username, defaults={"first_name": first_name, "is_staff": staff})
    if password:
        user.set_password(password)
    else:
        user.set_unusable_password()
    user.save()
    user.groups.set(Group.objects.filter(name__in=groups))
    return User.objects.get(pk=user.pk)  # پاک کردن کش مجوزها


def run(password=None, today=None):
    if Party.objects.filter(code=DEMO_SUPPLIER_CODE).exists():
        raise DemoAlreadyRun("گردش نمونه قبلاً روی این پایگاه داده اجرا شده است.")
    today = today or timezone.localdate()
    days = datetime.timedelta
    manager = _user("demo-manager", "مدیر نمونه", ["مدیر داروخانه"], password=password)
    pharmacist = _user("demo-pharmacist", "داروساز نمونه", ["داروساز مسئول فنی"], password=password)
    cashier = _user("demo-cashier", "صندوق‌دار نمونه", ["صندوق‌دار"], password=password)
    storekeeper = _user("demo-storekeeper", "انباردار نمونه", ["انباردار"], password=password)
    accountant = _user("demo-accountant", "حسابدار نمونه", ["حسابدار"], password=password)

    main = Warehouse.objects.get(code="MAIN")
    shelf = Location.objects.get(warehouse=main, code="GEN")
    fridge = Location.objects.get(warehouse=main, code="FRIDGE")
    strip, _ = Unit.objects.get_or_create(name="ورق")
    box, _ = Unit.objects.get_or_create(name="جعبه")
    piece, _ = Unit.objects.get_or_create(name="عدد")

    def product(slug, **kw):
        return Product.objects.create(slug=slug, **kw)

    otc = product("erp-demo-acetaminophen", kind=ProductKind.DRUG, name="استامینوفن ۳۲۵ ورق ۱۰ عددی (نمونه)",
                  sku="D-1001", irc="IRC-DEMO-1001", base_unit=strip, price=150_000, reorder_point=20)
    ProductPackage.objects.create(product=otc, unit=strip, factor=1, barcode="6260000000011")
    ProductPackage.objects.create(product=otc, unit=box, factor=10, barcode="6260000000028")
    rx = product("erp-demo-amoxicillin", kind=ProductKind.DRUG, name="آموکسی‌سیلین ۵۰۰ (نمونه)", sku="D-1002",
                 irc="IRC-DEMO-1002", base_unit=box, price=300_000, requires_prescription=True, max_qty_per_sale=3)
    insulin = product("erp-demo-insulin", kind=ProductKind.DRUG, name="انسولین قلمی (نمونه)", sku="D-1003",
                      irc="IRC-DEMO-1003", base_unit=piece, price=2_000_000, requires_prescription=True,
                      storage_condition=StorageCondition.COOL)
    device = product("erp-demo-bp-monitor", kind=ProductKind.EQUIPMENT, name="فشارسنج دیجیتال سریالی (نمونه)",
                     sku="E-2001", imed_code="IMED-DEMO-2001", device_class="IIa", base_unit=piece, price=30_000_000,
                     track_expiry=False, track_serial=True, tax_rate=10)

    supplier = Party.objects.create(kind=Party.Kind.SUPPLIER, code=DEMO_SUPPLIER_CODE, name="شرکت پخش دارویی (نمونه)",
                                    license_no="مجوز نمونه ۱۲۳")
    insurer = Party.objects.create(kind=Party.Kind.INSURER, code="DEMO-INS", name="سازمان بیمه‌گر (نمونه)")
    customer = Party.objects.create(kind=Party.Kind.CUSTOMER, code="DEMO-CUS", name="مشتری نسیه (نمونه)")
    owner = Party.objects.create(kind=Party.Kind.OTHER, code="DEMO-OWN", name="آورده مالک (نمونه)")

    # ۱. آورده نقدی مالک به بانک
    capital = Payment.objects.create(direction=Payment.Direction.RECEIVE, party=owner, method=PaymentMethod.BANK,
                                     amount=100_000_000, reference_no="DEMO-DEP-1", created_by=accountant)
    capital.post(accountant)

    # ۲. سفارش خرید (تأیید مدیر)
    po = PurchaseOrder.objects.create(supplier=supplier, created_by=manager)
    po_lines = {
        p.pk: PurchaseOrderLine.objects.create(document=po, product=p, quantity=q, unit_price=c)
        for p, q, c in ((otc, 100, 100_000), (rx, 30, 200_000), (insulin, 10, 1_500_000), (device, 2, 20_000_000))
    }
    po.post(manager)

    # ۳. رسید انبار (انباردار) با بچ، انقضا، محل و کد اصالت
    receipt = GoodsReceipt.objects.create(supplier=supplier, purchase_order=po, warehouse=main, delivery_note="DEMO-H-1",
                                          created_by=storekeeper)
    rows = [
        (otc, "A1", today + days(400), shelf, 60, 100_000),
        (otc, "A2", today + days(200), shelf, 40, 110_000),
        (rx, "B1", today + days(300), shelf, 30, 200_000),
        (insulin, "C1", today + days(150), fridge, 10, 1_500_000),
        (device, "SN1001", None, shelf, 1, 20_000_000),
        (device, "SN1002", None, shelf, 1, 20_000_000),
    ]
    for p, batch, expiry, location, qty, cost in rows:
        GoodsReceiptLine.objects.create(
            document=receipt, po_line=po_lines[p.pk], product=p, batch_number=batch, expiry_date=expiry, location=location,
            quantity=qty, unit_cost=cost, authenticity_code=f"UID-{batch}", authenticity_verified=True,
        )
    receipt.post(storekeeper)

    # ۴. فاکتور خرید با ۴۰۰ هزار ریال تخفیف (حسابدار) و ۵. پرداخت بخشی از بدهی
    invoice = PurchaseInvoice.objects.create(supplier=supplier, receipt=receipt, supplier_invoice_no="DEMO-F-7788",
                                             discount=400_000, created_by=accountant)
    fill_invoice_from_receipt(invoice)
    invoice.post(accountant)
    payment = Payment.objects.create(direction=Payment.Direction.PAY, party=supplier, method=PaymentMethod.BANK,
                                     amount=50_000_000, reference_no="DEMO-TRX-1", created_by=accountant)
    payment.post(accountant)

    # ۶. صندوق
    register = CashRegister.objects.create(name="صندوق نمونه", warehouse=main)
    session = sales.open_session(register, pharmacist, opening_cash=5_000_000)

    # فروش ۱: بدون نسخه، نقد (FEFO باید از بچ A2 با انقضای نزدیک‌تر بردارد)
    sale1 = sales.create_pos_invoice(session, pharmacist, "demo-sale-1")
    sales.add_line(sale1, otc, 10)
    SalesPayment.objects.create(document=sale1, method=SalesPayment.Method.CASH, amount=1_500_000)
    sale1.post(cashier)

    # فروش ۲: نسخه‌ای با سهم بیمه ۷۰٪ (فقط داروساز ثبت می‌کند)
    sale2 = sales.create_pos_invoice(session, pharmacist, "demo-sale-2")
    sales.add_line(sale2, rx, 2)
    sale2.prescription_number = "RX-DEMO-5566"
    sale2.prescriber_name = "دکتر نمونه"
    sale2.prescriber_council_no = "DEMO-0001"
    sale2.insurer = insurer
    sale2.insurance_amount = 420_000
    sale2.save()
    SalesPayment.objects.create(document=sale2, method=SalesPayment.Method.CASH, amount=180_000)
    sale2.post(pharmacist)

    # فروش ۳: تجهیزات سریالی با ۱۰٪ مالیات، نسیه
    sale3 = sales.create_pos_invoice(session, pharmacist, "demo-sale-3")
    sales.add_line(sale3, device, 1)
    sale3.customer = customer
    sale3.save()
    SalesPayment.objects.create(document=sale3, method=SalesPayment.Method.CREDIT, amount=33_000_000)
    sale3.post(pharmacist)

    # ۷. مرجوعی ۲ ورق از فروش ۱ (باز شده → انبار قرنطینه)، استرداد نقد
    ret = SalesReturn.objects.create(invoice=sale1, reason="بسته باز شده", restock=False,
                                     refund_method=SalesReturn.Refund.CASH, session=session, created_by=pharmacist)
    SalesReturnLine.objects.create(document=ret, invoice_line=sale1.lines.get(), quantity=2)
    ret.post(pharmacist)

    # ۸. سفارش آنلاین ۵ ورق (رزرو) و تحویل (فاکتور آنلاین خودکار)
    from apps.shop.forms import CheckoutForm
    from apps.shop.views import _create_order

    buyer = _user("09129990001", "خریدار آنلاین", staff=False)
    form = CheckoutForm(data={"full_name": "خریدار آنلاین", "mobile": "09129990001", "delivery_method": "pickup", "consent": "on"})
    assert form.is_valid(), form.errors
    otc.refresh_from_db()
    order = _create_order(buyer, form, [{"product": otc, "quantity": 5, "total": otc.price * 5}])
    stock_after_reservation = Product.objects.get(pk=otc.pk).stock
    from apps.audit.context import acting_as

    with acting_as(manager):
        order.status = Order.Status.DELIVERED
        order.save()

    # ۹. هزینه اجاره و ۱۰. بستن صندوق با ۱۰ هزار ریال کسری
    rent = Expense.objects.create(account=Account.objects.get(code="6102"), amount=10_000_000, method=PaymentMethod.BANK,
                                  description="اجاره ماه (نمونه)", created_by=accountant)
    rent.post(accountant)
    expected_cash = sales.session_expected_cash(session)
    session = sales.close_session(session, expected_cash - 10_000, pharmacist)

    # ۱۱. فراخوان بچ انسولین
    inv.change_batch_status(Batch.objects.get(product=insulin, number="C1"), Batch.Status.RECALLED,
                            "فراخوان نمونه سازمان غذا و دارو", pharmacist, reference="DEMO-RECALL-1")

    for p in (otc, rx, insulin, device):
        p.refresh_from_db()
    pnl = acc.profit_and_loss()
    _, totals = acc.trial_balance()
    return {
        "products": {"otc": otc, "rx": rx, "insulin": insulin, "device": device},
        "parties": {"supplier": supplier, "insurer": insurer, "customer": customer},
        "docs": {"po": po, "receipt": receipt, "invoice": invoice, "payment": payment, "sales": [sale1, sale2, sale3],
                 "return": ret, "order": order, "online_invoice": SalesInvoice.objects.get(order=order), "expense": rent},
        "session": session,
        "expected_cash": expected_cash,
        "stock_after_reservation": stock_after_reservation,
        "pnl": pnl,
        "trial_totals": totals,
        "inventory_gl": acc.account_balance(acc.INVENTORY),
        "inventory_ledger": StockMove.objects.aggregate(v=Sum("total_cost"))["v"] or 0,
        "supplier_payable": supplier_payable(supplier),
        "ap_balance": -acc.account_balance(acc.AP_SUPPLIERS),
        "ar_insurers": acc.account_balance(acc.AR_INSURERS),
        "ar_customers": acc.account_balance(acc.AR_CUSTOMERS),
        "availability_drift": inv.reconcile_available(),
        "alert_counts": alerts.counts(),
    }

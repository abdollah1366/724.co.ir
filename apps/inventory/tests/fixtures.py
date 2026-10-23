"""داده‌های مشترک آزمون‌های سامانه مدیریت داروخانه."""

import datetime

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.utils import timezone

from apps.accounting.models import Party
from apps.inventory.models import Location, Warehouse
from apps.purchasing.models import GoodsReceipt, GoodsReceiptLine
from apps.sales import services as sales
from apps.sales.models import CashRegister, SalesPayment
from apps.shop.models import Product, ProductKind, StorageCondition

TODAY = None


def today():
    return timezone.localdate()


def days(n):
    return today() + datetime.timedelta(days=n)


def staff(username, *roles, superuser=False):
    User = get_user_model()
    if superuser:
        return User.objects.create_superuser(username, password="x-strong-pass-1")
    user = User.objects.create_user(username, password="x-strong-pass-1", is_staff=True)
    user.groups.set(Group.objects.filter(name__in=roles))
    return User.objects.get(pk=user.pk)


def main():
    return Warehouse.objects.get(code="MAIN")


def quarantine():
    return Warehouse.objects.get(code="QRN")


def shelf():
    return Location.objects.get(warehouse__code="MAIN", code="GEN")


def fridge():
    return Location.objects.get(warehouse__code="MAIN", code="FRIDGE")


def drug(slug="drug-a", price=150_000, **kw):
    kw.setdefault("name", f"داروی {slug}")
    kw.setdefault("irc", f"IRC-{slug}")
    return Product.objects.create(kind=ProductKind.DRUG, slug=slug, price=price, **kw)


def device(slug="device-a", price=30_000_000, **kw):
    kw.setdefault("name", f"دستگاه {slug}")
    kw.setdefault("track_expiry", False)
    return Product.objects.create(kind=ProductKind.EQUIPMENT, slug=slug, price=price, **kw)


def cold_drug(slug="insulin", **kw):
    return drug(slug, price=2_000_000, storage_condition=StorageCondition.COOL, requires_prescription=True, **kw)


def supplier(name="پخش آزمایشی", code=None):
    return Party.objects.create(kind=Party.Kind.SUPPLIER, name=name, code=code)


def receive(product, qty, cost, batch="B1", expiry=None, location=None, user=None, sup=None, post=True, **line):
    """رسید خرید ساده برای آماده کردن موجودی."""
    sup = sup or Party.objects.filter(kind=Party.Kind.SUPPLIER).first() or supplier()
    receipt = GoodsReceipt.objects.create(supplier=sup, warehouse=main())
    if expiry is None and product.track_expiry:
        expiry = days(365)
    GoodsReceiptLine.objects.create(
        document=receipt, product=product, batch_number=batch, expiry_date=expiry, location=location or shelf(),
        quantity=qty, unit_cost=cost, authenticity_code=line.pop("authenticity_code", f"UID-{batch}"),
        authenticity_verified=line.pop("authenticity_verified", True), **line,
    )
    if post:
        receipt.post(user)
    return receipt


def register():
    return CashRegister.objects.get_or_create(name="صندوق آزمون", defaults={"warehouse": main()})[0]


def open_session(user, cash=0):
    return sales.open_session(register(), user, cash)


def pos_sale(session, user, items, payments=None, post=True, **fields):
    """items: [(product, qty)], payments: [(method, amount)] (پیش‌فرض: کل مبلغ نقد)."""
    invoice = sales.create_pos_invoice(session, user)
    for product, qty in items:
        sales.add_line(invoice, product, qty)
    for key, value in fields.items():
        setattr(invoice, key, value)
    invoice.save()
    for method, amount in payments or [(SalesPayment.Method.CASH, None)]:
        SalesPayment.objects.create(document=invoice, method=method, amount=invoice.total if amount is None else amount)
    if post:
        invoice.post(user)
    return invoice

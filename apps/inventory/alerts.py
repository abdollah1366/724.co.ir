"""هشدارهای انبار: کمبود، نزدیک انقضا، منقضی، فراخوان/قرنطینه/توقیف، کالای غیرقابل‌فروش و نقص اطلاعات."""

import datetime

from django.conf import settings
from django.db.models import F, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.shop.models import Product, ProductKind

from . import services
from .models import Batch, StockBalance

BLOCKED = [Batch.Status.RECALLED, Batch.Status.QUARANTINE, Batch.Status.BLOCKED]


def _balances():
    return StockBalance.objects.filter(quantity__gt=0).select_related("product", "warehouse", "location", "batch")


def low_stock():
    today = timezone.localdate()
    sellable = Q(balances__warehouse__is_sellable=True, balances__batch__status=Batch.Status.ACTIVE) & (
        Q(balances__batch__expiry_date__isnull=True) | Q(balances__batch__expiry_date__gte=today)
    )
    return (
        Product.objects.filter(is_active=True, reorder_point__gt=0)
        .annotate(sellable_qty=Coalesce(Sum("balances__quantity", filter=sellable), Value(0)))
        .filter(sellable_qty__lt=F("reorder_point")).order_by("sellable_qty")
    )


def near_expiry(days=None):
    days = days or settings.PHARMACY_RULES.get("expiry_alert_days", 90)
    today = timezone.localdate()
    return _balances().filter(
        batch__expiry_date__gte=today, batch__expiry_date__lte=today + datetime.timedelta(days=days)
    ).order_by("batch__expiry_date")


def expired():
    today = timezone.localdate()
    return _balances().filter(Q(batch__expiry_date__lt=today) | Q(batch__status=Batch.Status.EXPIRED)).order_by("batch__expiry_date")


def blocked():
    return _balances().filter(batch__status__in=BLOCKED).order_by("batch__status")


def non_sellable_stock():
    """کالای موجود در انبار قرنطینه/مرجوعی یا کالایی که فروش آن متوقف شده."""
    return _balances().filter(Q(warehouse__is_sellable=False) | Q(product__is_sellable=False))


def data_issues():
    issues = []
    for product in Product.objects.filter(is_active=True, kind=ProductKind.DRUG, irc=""):
        issues.append((product, "کد IRC ثبت نشده"))
    for product in Product.objects.filter(is_active=True, kind=ProductKind.EQUIPMENT, imed_code=""):
        issues.append((product, "کد اداره کل تجهیزات پزشکی ثبت نشده"))
    for balance in _balances().filter(batch__number="LEGACY"):
        issues.append((balance.product, f"موجودی قدیمی بدون بچ و بها ({balance.quantity}) — انبارگردانی لازم است"))
    for balance in _balances().filter(product__track_expiry=True, batch__expiry_date__isnull=True).exclude(batch__number="LEGACY"):
        issues.append((balance.product, f"بچ {balance.batch.number} تاریخ انقضا ندارد"))
    return issues


def all_alerts():
    return {
        "low_stock": list(low_stock()),
        "near_expiry": list(near_expiry()),
        "expired": list(expired()),
        "blocked": list(blocked()),
        "non_sellable": list(non_sellable_stock()),
        "data_issues": data_issues(),
        "availability_drift": services.reconcile_available(fix=False),
    }


def counts():
    data = all_alerts()
    return {key: len(value) for key, value in data.items()}

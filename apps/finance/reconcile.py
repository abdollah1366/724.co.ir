"""کنترل‌های مغایرت مالی (صفحه «کنترل‌های مالی» و دستور python manage.py finance_reconcile).

هر کنترل از داده‌های ثبت‌شده محاسبه می‌شود و چیزی را تغییر نمی‌دهد؛ اختلاف فقط گزارش و در لاگ با سطح ERROR ثبت می‌شود.
"""

import datetime
import logging

from django.db.models import Sum
from django.utils import timezone

from apps.accounting import services as acc
from apps.audit.documents import DocStatus
from apps.core import events, flags
from apps.core.models import OutboxEvent

from .models import Invoice, PayrollRun, ProviderSettlement, Receipt, Refund
from .services import billing

logger = logging.getLogger("apps.finance")


def _check(name, ok, detail):
    return {"name": name, "ok": bool(ok), "detail": detail}


def run_checks(stale_minutes=60):
    results = []
    _, totals = acc.trial_balance()
    results.append(_check("تراز آزمایشی متوازن است", totals["debit"] == totals["credit"],
                          f"بدهکار {totals['debit']:,} / بستانکار {totals['credit']:,}"))
    unbalanced = acc.unbalanced_entries()
    results.append(_check("همه اسناد حسابداری متوازن‌اند", not unbalanced, "، ".join(unbalanced[:10]) or "—"))

    sub = billing.subledger_totals()
    gl_ar = acc.account_balance(acc.AR_PATIENTS)
    results.append(_check("مطالبات بیماران: دفتر کل = صورتحساب‌ها", gl_ar == sub["ar_patients"],
                          f"دفتر کل {gl_ar:,} / زیردفتر {sub['ar_patients']:,}"))
    gl_adv = -acc.account_balance(acc.PATIENT_ADVANCES)
    results.append(_check("علی‌الحساب بیماران: دفتر کل = دریافت‌ها", gl_adv == sub["advances"],
                          f"دفتر کل {gl_adv:,} / زیردفتر {sub['advances']:,}"))

    from apps.inventory.models import StockMove

    kardex = StockMove.objects.aggregate(v=Sum("total_cost"))["v"] or 0
    gl_inv = acc.account_balance(acc.INVENTORY)
    results.append(_check("موجودی کالا: دفتر کل = کاردکس", kardex == gl_inv, f"دفتر کل {gl_inv:,} / کاردکس {kardex:,}"))

    missing = []
    for model in (Invoice, Receipt, Refund, ProviderSettlement, PayrollRun):
        label = model._meta.label_lower
        posted = set(model.objects.exclude(status=DocStatus.DRAFT).values_list("pk", flat=True))
        with_entry = set(acc.JournalEntry.objects.filter(source_type=label, is_reversal=False).values_list("source_id", flat=True))
        zero = _zero_docs(model)
        missing += [f"{label}#{pk}" for pk in posted - with_entry - zero]
    results.append(_check("هر سند مالی ثبت‌شده سند حسابداری دارد", not missing, "، ".join(missing[:10]) or "—"))

    health = events.health()
    results.append(_check("رویداد متوقف (نیازمند بررسی) وجود ندارد", not health["dead"], f"{health['dead']} رویداد"))
    results.append(_check(f"رویداد معوق قدیمی‌تر از {stale_minutes} دقیقه وجود ندارد (به‌جز قابلیت‌های خاموش)",
                          _stale(stale_minutes) == 0, f"{_stale(stale_minutes)} رویداد؛ {health['held']} در انتظار قابلیت خاموش"))
    for item in results:
        if not item["ok"]:
            logger.error("finance_check_failed check=%s detail=%s", item["name"], item["detail"])
    return results


def _zero_docs(model):
    if model is Invoice:
        return set(model.objects.filter(gross=0).values_list("pk", flat=True))
    if model is ProviderSettlement:
        return set(model.objects.filter(total=0).values_list("pk", flat=True))
    return set()


def _stale(minutes):
    held = [t for t, s in events.subscriptions().items() if s.flag and not flags.is_enabled(s.flag)]
    limit = timezone.now() - datetime.timedelta(minutes=minutes)
    return (OutboxEvent.objects.filter(status__in=[OutboxEvent.Status.PENDING, OutboxEvent.Status.FAILED], created_at__lt=limit)
            .exclude(event_type__in=held).count())

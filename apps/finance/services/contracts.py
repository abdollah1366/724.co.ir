"""چرخه قرارداد: نسخه جدید، ارسال، تأیید مستقل، فعال‌سازی، تعلیق، خاتمه، انقضا و تمدید خودکار. همه در گزارش تغییرات."""

import datetime

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.audit import log
from apps.audit.documents import DocumentError
from apps.audit.models import AuditLog

from ..models import Contract, ContractRule, ContractVersion

ALLOWED = {
    Contract.Status.DRAFT: {Contract.Status.ACTIVE},
    Contract.Status.ACTIVE: {Contract.Status.SUSPENDED, Contract.Status.TERMINATED, Contract.Status.EXPIRED},
    Contract.Status.SUSPENDED: {Contract.Status.ACTIVE, Contract.Status.TERMINATED},
}


def _record(obj, message, user):
    log.record(AuditLog.Action.STATUS, obj, message=message[:500], user=user)


@transaction.atomic
def set_status(contract, status, user, reason, on_date=None):
    contract = Contract.objects.select_for_update().get(pk=contract.pk)
    if status not in ALLOWED.get(contract.status, set()):
        raise DocumentError(f"تغییر وضعیت از «{contract.get_status_display()}» به این وضعیت مجاز نیست.")
    if status == Contract.Status.ACTIVE and not contract.versions.filter(status=ContractVersion.Status.APPROVED).exists():
        raise DocumentError("قرارداد بدون نسخه تأییدشده فعال نمی‌شود.")
    fields = {"status": status, "status_reason": (reason or "")[:300]}
    if status == Contract.Status.TERMINATED:
        fields["terminated_on"] = on_date or timezone.localdate()
    Contract.objects.filter(pk=contract.pk).update(**fields)
    contract.refresh_from_db()
    _record(contract, f"وضعیت قرارداد: {contract.get_status_display()} — {reason}", user)
    return contract


@transaction.atomic
def new_version(contract, user, effective_from=None):
    """نسخه پیش‌نویس جدید با کپی شرایط و قواعد آخرین نسخه؛ نسخه‌های قبلی دست نمی‌خورند."""
    last = contract.versions.order_by("-version").first()
    version = ContractVersion(contract=contract, effective_from=effective_from or timezone.localdate(), created_by=user)
    if last:
        for field in ("share_base", "fixed_monthly", "period_floor", "period_cap", "payment_terms_days", "terms"):
            setattr(version, field, getattr(last, field))
    version.save()
    if last:
        ContractRule.objects.bulk_create(
            ContractRule(version=version, module=r.module, service_code=r.service_code, basis=r.basis, percent=r.percent,
                         amount=r.amount) for r in last.rules.all()
        )
    return version


def submit_version(version, user):
    if version.status not in (ContractVersion.Status.DRAFT, ContractVersion.Status.REJECTED):
        raise DocumentError("فقط نسخه پیش‌نویس ارسال می‌شود.")
    if version.contract.kind == Contract.Kind.PROVIDER and not version.rules.exists() and not version.fixed_monthly:
        raise DocumentError("قرارداد پزشک دست‌کم یک قاعده سهم یا مبلغ ثابت ماهانه لازم دارد.")
    if version.effective_from < version.contract.start_date:
        raise DocumentError("تاریخ مؤثر نسخه پیش از شروع قرارداد است.")
    _change(version, ContractVersion.Status.SUBMITTED, user, "ارسال نسخه قرارداد برای تأیید")
    return version


@transaction.atomic
def approve_version(version, user):
    if not user.has_perm("finance.approve_contractversion"):
        raise DocumentError("مجوز تأیید قرارداد ندارید.")
    version = ContractVersion.objects.select_for_update().get(pk=version.pk)
    if version.status != ContractVersion.Status.SUBMITTED:
        raise DocumentError("فقط نسخه ارسال‌شده تأیید می‌شود.")
    if settings.FINANCE_RULES["four_eyes"] and version.created_by_id == user.pk:
        raise DocumentError("تهیه‌کننده نسخه قرارداد نمی‌تواند خودش آن را تأیید کند.")
    clash = ContractVersion.objects.filter(contract=version.contract, status=ContractVersion.Status.APPROVED,
                                           effective_from=version.effective_from)
    if clash.exists():
        raise DocumentError("نسخه تأییدشده دیگری با همین تاریخ مؤثر وجود دارد؛ تاریخ مؤثر متفاوت بگذارید.")
    version.approved_by, version.approved_at = user, timezone.now()
    _change(version, ContractVersion.Status.APPROVED, user, "تأیید نسخه قرارداد (تأیید کاربر، نه امضای الکترونیکی)",
            extra=["approved_by", "approved_at"])
    return version


def reject_version(version, user, note):
    if not user.has_perm("finance.approve_contractversion"):
        raise DocumentError("مجوز تأیید قرارداد ندارید.")
    if version.status != ContractVersion.Status.SUBMITTED:
        raise DocumentError("فقط نسخه ارسال‌شده رد می‌شود.")
    version.review_note = (note or "")[:300]
    _change(version, ContractVersion.Status.REJECTED, user, f"رد نسخه قرارداد: {note}", extra=["review_note"])
    return version


def _change(version, status, user, message, extra=()):
    version.status = status
    version._status_change = True
    try:
        version.save(update_fields=["status", *extra])
    finally:
        version._status_change = False
    _record(version, message, user)


def tick(today=None):
    """انقضای قراردادهای سررسیده و تمدید خودکار (python manage.py finance_tick روزانه)."""
    today = today or timezone.localdate()
    stats = {"renewed": 0, "expired": 0}
    for contract in Contract.objects.filter(status=Contract.Status.ACTIVE, end_date__lt=today):
        if contract.auto_renew:
            new_end = _add_months(contract.end_date, contract.renewal_months)
            Contract.objects.filter(pk=contract.pk).update(end_date=new_end, status_reason=f"تمدید خودکار تا {new_end}")
            _record(contract, f"تمدید خودکار قرارداد تا {new_end}", None)
            stats["renewed"] += 1
        else:
            set_status(contract, Contract.Status.EXPIRED, None, "پایان مدت قرارداد")
            stats["expired"] += 1
    return stats


def _add_months(date, months):
    month = date.month - 1 + months
    year = date.year + month // 12
    month = month % 12 + 1
    day = min(date.day, [31, 29 if year % 4 == 0 and (year % 100 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31,
                         30, 31][month - 1])
    return datetime.date(year, month, day)

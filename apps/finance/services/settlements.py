"""محاسبه و تسویه سهم پزشکان از روی قرارداد نسخه‌دار.

محاسبه deterministic است: فقط از داده‌های ثبت‌شده (صورتحساب، تخصیص دریافت، استرداد) و نسخه تأییدشده قرارداد که در تاریخ
خدمت مؤثر بوده استفاده می‌کند، ترتیب ثابت دارد و هر ردیف جزئیات محاسبه‌اش را ذخیره می‌کند. هر قلم با کلید یکتا
(dedupe_key) فقط در یک تسویه فعال می‌آید؛ پس تسویه مضاعف حتی با دو درخواست هم‌زمان ممکن نیست (قید پایگاه داده).

تفاوت مفاهیم حفظ می‌شود: مبلغ خدمت (gross)، مبلغ صورتحساب (net پس از تخفیف)، وصول‌شده (تخصیص دریافت منهای استرداد) و
درآمد قابل تسویه (سهم محاسبه‌شده پزشک).
"""

import datetime
import hashlib
import json

import jdatetime
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounting import services as acc
from apps.audit import log
from apps.audit.documents import DocStatus, DocumentError
from apps.audit.models import AuditLog

from ..models import (
    Contract, ContractRule, ContractVersion, InvoiceLine, PaymentAllocation, ProviderSettlement, Refund, SettlementLine,
)
from ..money import percent_of, prorate, split_proportional

EXPENSE_ACCOUNT = "6107"
DEFAULT_CENTER = "CLINIC"
Kind = SettlementLine.Kind
Basis = ContractRule.Basis


def month_bounds(year, month):
    start = jdatetime.date(year, month, 1)
    end = jdatetime.date(year + (month == 12), month % 12 + 1, 1) - jdatetime.timedelta(days=1)
    return start.togregorian(), end.togregorian()


class Terms:
    """نسخه‌های تأییدشده یک قرارداد با قواعدشان؛ یک بار خوانده می‌شود."""

    def __init__(self, contract):
        self.contract = contract
        self.versions = list(
            ContractVersion.objects.filter(contract=contract, status=ContractVersion.Status.APPROVED)
            .prefetch_related("rules").order_by("effective_from", "version")
        )

    def version_at(self, date):
        if not self.contract.covers(date):
            return None
        current = None
        for version in self.versions:
            if version.effective_from <= date:
                current = version
        return current

    @staticmethod
    def rule_for(version, module, service_code):
        candidates = [r for r in version.rules.all() if r.matches(module, service_code)]
        candidates.sort(key=lambda r: (-r.specificity, r.pk))
        return candidates[0] if candidates else None


def _base(version, line):
    return line.gross if version.share_base == ContractVersion.ShareBase.GROSS else line.net


def _rule_amount(rule, base, quantity):
    if rule.basis == Basis.FIXED_PER_SERVICE:
        return rule.amount * quantity, f"{rule.amount:,} × {quantity}"
    return percent_of(base, rule.percent), f"{base:,} × {rule.percent}٪"


def _row(kind, key, amount, description, base=0, line=None, version=None, rule=None, **detail):
    return {"kind": kind, "dedupe_key": key, "amount": int(amount), "base_amount": int(base), "description": description[:300],
            "invoice_line": line, "contract_version": version, "rule": rule, "detail": detail}


def _taken(exclude_settlement=None):
    qs = SettlementLine.objects.filter(is_void=False)
    if exclude_settlement is not None:
        qs = qs.exclude(settlement=exclude_settlement)
    return qs


def compute(contract, year, month, exclude_settlement=None):
    """ردیف‌های قابل تسویه این قرارداد برای ماه شمسی (بدون ذخیره). خروجی: فهرست dict به ترتیب ثابت."""
    if contract.kind != Contract.Kind.PROVIDER or not contract.provider_id:
        raise DocumentError("تسویه فقط برای قرارداد پزشک/ارائه‌دهنده با شناسه پزشک ممکن است.")
    start, end = month_bounds(year, month)
    terms = Terms(contract)
    taken = {}
    for key, amount in _taken(exclude_settlement).filter(
            Q(dedupe_key__startswith="billed:") | Q(dedupe_key__startswith="coll:") | Q(dedupe_key__startswith="refund:")
            | Q(dedupe_key__startswith="billed-rev:") | Q(dedupe_key__startswith="coll-rev:")
            | Q(dedupe_key__startswith="refund-rev:") | Q(dedupe_key__startswith=f"fixed:{contract.pk}:")
            | Q(dedupe_key__startswith=f"floorcap:{contract.pk}:")).values_list("dedupe_key", "amount"):
        taken[key] = amount
    rows = []

    # ۱) سهم از صورتحساب (و برگشت سهم صورتحساب‌های ابطال‌شده پس از تسویه)
    lines = (
        InvoiceLine.objects.filter(provider_id=contract.provider_id, invoice__date__gte=contract.start_date,
                                   invoice__date__lte=end, invoice__status__in=[DocStatus.POSTED, DocStatus.REVERSED])
        .select_related("invoice").order_by("invoice__date", "invoice_id", "id")
    )
    for line in lines:
        invoice = line.invoice
        version = terms.version_at(invoice.date)
        rule = terms.rule_for(version, invoice.module, line.service_code) if version else None
        if rule is None or rule.basis == Basis.PERCENT_COLLECTED:
            continue
        key, rev_key = f"billed:{line.pk}", f"billed-rev:{line.pk}"
        if invoice.status == DocStatus.POSTED and key not in taken:
            base = _base(version, line)
            amount, formula = _rule_amount(rule, base, line.quantity)
            rows.append(_row(Kind.BILLED, key, amount, f"{invoice.number} {line.description}", base, line, version, rule,
                             invoice=invoice.number, date=str(invoice.date), code=line.service_code, formula=formula,
                             center=invoice.cost_center.code))
        elif invoice.status == DocStatus.REVERSED and key in taken and rev_key not in taken \
                and invoice.reversed_at and timezone.localdate(invoice.reversed_at) <= end:
            rows.append(_row(Kind.BILLED_REVERSAL, rev_key, -taken[key], f"ابطال {invoice.number} {line.description}", 0, line,
                             version, rule, invoice=invoice.number, reverses=key, center=invoice.cost_center.code))

    # ۲) سهم از وصولی: هر تخصیص دریافت به نسبت سهم بیمار ردیف‌ها تقسیم می‌شود
    allocations = (
        PaymentAllocation.objects.filter(invoice__lines__provider_id=contract.provider_id,
                                         invoice__date__gte=contract.start_date, receipt__date__lte=end,
                                         receipt__status__in=[DocStatus.POSTED, DocStatus.REVERSED])
        .select_related("receipt", "invoice", "invoice__cost_center").distinct().order_by("receipt__date", "receipt_id", "id")
    )
    for allocation in allocations:
        rows += _collection_rows(terms, allocation.invoice, allocation.amount, f"coll:{allocation.pk}",
                                 allocation.receipt.status == DocStatus.REVERSED, taken, end,
                                 allocation.receipt.reversed_at, allocation.receipt.number, Kind.COLLECTED, Kind.COLLECTED_REVERSAL)
    refunds = (
        Refund.objects.filter(invoice__lines__provider_id=contract.provider_id, invoice__date__gte=contract.start_date,
                              date__lte=end, status__in=[DocStatus.POSTED, DocStatus.REVERSED])
        .select_related("invoice", "invoice__cost_center").distinct().order_by("date", "id")
    )
    for refund in refunds:
        rows += _collection_rows(terms, refund.invoice, -refund.amount, f"refund:{refund.pk}",
                                 refund.status == DocStatus.REVERSED, taken, end, refund.reversed_at, refund.number,
                                 Kind.COLLECTED_REVERSAL, Kind.COLLECTED)

    # ۳) مبلغ ثابت ماهانه به نسبت روزهایی از ماه که قرارداد برقرار بوده
    version = terms.version_at(end) or next((terms.version_at(d) for d in _days(start, end) if terms.version_at(d)), None)
    fixed_key = f"fixed:{contract.pk}:{year}-{month:02d}"
    if version and version.fixed_monthly and fixed_key not in taken:
        days = _days(start, end)
        covered = sum(1 for d in days if terms.version_at(d))
        amount = prorate(version.fixed_monthly, covered, len(days))
        rows.append(_row(Kind.FIXED, fixed_key, amount, f"مبلغ ثابت {year}/{month:02d}", version.fixed_monthly, None, version,
                         formula=f"{version.fixed_monthly:,} × {covered}/{len(days)} روز", center=DEFAULT_CENTER))

    # ۴) کف و سقف ماهانه روی جمع این ماه
    floor_key = f"floorcap:{contract.pk}:{year}-{month:02d}"
    if version and (version.period_floor is not None or version.period_cap is not None) and floor_key not in taken:
        subtotal = sum(r["amount"] for r in rows)
        if version.period_floor is not None and subtotal < version.period_floor:
            rows.append(_row(Kind.FLOOR_CAP, floor_key, version.period_floor - subtotal, "تکمیل تا کف پرداخت ماهانه", subtotal,
                             None, version, formula=f"کف {version.period_floor:,} − {subtotal:,}", center=DEFAULT_CENTER))
        elif version.period_cap is not None and subtotal > version.period_cap:
            rows.append(_row(Kind.FLOOR_CAP, floor_key, version.period_cap - subtotal, "کسر مازاد سقف پرداخت ماهانه", subtotal,
                             None, version, formula=f"سقف {version.period_cap:,} − {subtotal:,}", center=DEFAULT_CENTER))
    return rows


def _days(start, end):
    return [start + datetime.timedelta(days=i) for i in range((end - start).days + 1)]


def _collection_rows(terms, invoice, amount, prefix, is_reversed, taken, end, reversed_at, ref, kind, rev_kind):
    lines = [line for line in invoice.lines.order_by("id")]
    shares = split_proportional(amount, [line.patient_share for line in lines])
    rows = []
    for line, share in zip(lines, shares):
        if line.provider_id != terms.contract.provider_id or not share:
            continue
        version = terms.version_at(invoice.date)
        rule = terms.rule_for(version, invoice.module, line.service_code) if version else None
        if rule is None or rule.basis != Basis.PERCENT_COLLECTED:
            continue
        key, rev_key = f"{prefix}:{line.pk}", f"{prefix.split(':')[0]}-rev:{prefix.split(':')[1]}:{line.pk}"
        center = invoice.cost_center.code
        if not is_reversed and key not in taken:
            value = percent_of(share, rule.percent)
            rows.append(_row(kind, key, value, f"{ref} بابت {invoice.number} {line.description}", share, line, version, rule,
                             invoice=invoice.number, source=ref, formula=f"{share:,} × {rule.percent}٪", center=center))
        elif is_reversed and key in taken and rev_key not in taken and reversed_at and timezone.localdate(reversed_at) <= end:
            rows.append(_row(rev_kind, rev_key, -taken[key], f"برگشت {ref} بابت {invoice.number}", 0, line, version, rule,
                             invoice=invoice.number, reverses=key, center=center))
    return rows


def fingerprint(rows):
    data = [[r["dedupe_key"], r["amount"], r["base_amount"]] for r in rows]
    return hashlib.sha256(json.dumps(sorted(data), ensure_ascii=False).encode()).hexdigest()


def _computed(settlement):
    return [r for r in compute(settlement.contract, settlement.year, settlement.month, exclude_settlement=settlement)]


@transaction.atomic
def calculate(settlement, user):
    """محاسبه (یا محاسبه دوباره) تسویه پیش‌نویس. ردیف‌های دستی (پاداش/کسورات) حفظ و تأییدها باطل می‌شوند."""
    settlement = ProviderSettlement.objects.select_for_update().get(pk=settlement.pk)
    if settlement.status != DocStatus.DRAFT:
        raise DocumentError("فقط تسویه پیش‌نویس محاسبه می‌شود.")
    settlement.lines.exclude(kind=Kind.ADJUSTMENT).delete()
    rows = _computed(settlement)
    SettlementLine.objects.bulk_create(SettlementLine(settlement=settlement, **row) for row in rows)
    total = sum(line.amount for line in settlement.lines.all())
    start, end = month_bounds(settlement.year, settlement.month)
    ProviderSettlement.objects.filter(pk=settlement.pk).update(
        total=total, calculated_at=timezone.now(), calc_hash=fingerprint(rows), period_start=start, period_end=end,
        finance_approved_by=None, finance_approved_at=None, manager_approved_by=None, manager_approved_at=None,
    )
    settlement.refresh_from_db()
    log.record(AuditLog.Action.STATUS, settlement, message=f"محاسبه تسویه: {total:,} ریال، {len(rows)} ردیف", user=user)
    return settlement


@transaction.atomic
def create_settlement(contract, year, month, user):
    start, end = month_bounds(year, month)
    settlement = ProviderSettlement.objects.create(contract=contract, year=year, month=month, period_start=start,
                                                   period_end=end, created_by=user, date=end)
    return calculate(settlement, user)


def add_adjustment(settlement, amount, reason, user):
    import uuid

    if settlement.status != DocStatus.DRAFT:
        raise DocumentError("فقط تسویه پیش‌نویس قابل تغییر است.")
    if not (reason or "").strip() or not amount:
        raise DocumentError("مبلغ و علت پاداش یا کسورات را وارد کنید.")
    SettlementLine.objects.create(settlement=settlement, kind=Kind.ADJUSTMENT, amount=int(amount), description=reason[:300],
                                  dedupe_key=f"adj:{settlement.pk}:{uuid.uuid4().hex[:12]}", detail={"by": user.pk})
    return calculate(settlement, user)


def _four_eyes(settlement, user, *others):
    if not settings.FINANCE_RULES["four_eyes"]:
        return
    if user.pk in {settlement.created_by_id, *[o for o in others if o]}:
        raise DocumentError("این تأیید باید توسط کاربر دیگری غیر از ایجادکننده و تأییدکننده قبلی انجام شود.")


@transaction.atomic
def approve_finance(settlement, user):
    if not user.has_perm("finance.approve_settlement_finance"):
        raise DocumentError("مجوز تأیید مالی تسویه ندارید.")
    settlement = ProviderSettlement.objects.select_for_update().get(pk=settlement.pk)
    if settlement.status != DocStatus.DRAFT or not settlement.calculated_at:
        raise DocumentError("تسویه باید پیش‌نویس و محاسبه‌شده باشد.")
    if settlement.finance_approved_at:
        raise DocumentError("تأیید مالی قبلاً انجام شده است.")
    _four_eyes(settlement, user)
    ProviderSettlement.objects.filter(pk=settlement.pk).update(finance_approved_by=user, finance_approved_at=timezone.now())
    log.record(AuditLog.Action.STATUS, settlement, message="تأیید مالی تسویه", user=user)
    settlement.refresh_from_db()
    return settlement


@transaction.atomic
def approve_manager(settlement, user):
    if not user.has_perm("finance.approve_settlement_manager"):
        raise DocumentError("مجوز تأیید مدیریتی تسویه ندارید.")
    settlement = ProviderSettlement.objects.select_for_update().get(pk=settlement.pk)
    if not settlement.finance_approved_at:
        raise DocumentError("ابتدا تأیید مالی لازم است.")
    if settlement.manager_approved_at:
        raise DocumentError("تأیید مدیریتی قبلاً انجام شده است.")
    _four_eyes(settlement, user, settlement.finance_approved_by_id)
    ProviderSettlement.objects.filter(pk=settlement.pk).update(manager_approved_by=user, manager_approved_at=timezone.now())
    log.record(AuditLog.Action.STATUS, settlement, message="تأیید مدیریتی تسویه", user=user)
    settlement.refresh_from_db()
    return settlement


def validate_settlement_post(settlement):
    if not (settlement.finance_approved_at and settlement.manager_approved_at):
        raise DocumentError("تسویه هنوز تأیید مالی و مدیریتی نشده است.")
    if fingerprint(_computed(settlement)) != settlement.calc_hash:
        raise DocumentError("داده‌های مبنا پس از محاسبه تغییر کرده است؛ دوباره محاسبه و تأیید کنید.")
    expected = sum(line.amount for line in settlement.lines.all())
    if expected != settlement.total:
        raise DocumentError("جمع ردیف‌ها با مبلغ تسویه یکی نیست؛ دوباره محاسبه کنید.")


def post_settlement(settlement, user):
    contract = settlement.contract
    by_center = {}
    for line in settlement.lines.all():
        center = line.detail.get("center") or DEFAULT_CENTER
        by_center[center] = by_center.get(center, 0) + line.amount
    text = f"تسویه {settlement.number} {contract.provider_name or contract.party} {settlement.year}/{settlement.month:02d}"
    lines = [(EXPENSE_ACCOUNT, amount, 0, None, text, center) for center, amount in sorted(by_center.items())]
    lines.append((acc.AP_PROVIDERS, 0, settlement.total, contract.party, text, None))
    acc.post_entry(acc.source_of(settlement), lines, text, settlement.date, user, cost_center="")


def reverse_settlement(settlement, user):
    settlement.lines.update(is_void=True)
    acc.reverse_entry_for(settlement, user)


def provider_contracts(provider_id):
    return Contract.objects.filter(kind=Contract.Kind.PROVIDER, provider_id=provider_id).order_by("-start_date")

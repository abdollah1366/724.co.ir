"""گردش کار فنی آزمایشگاه: نمونه، ثبت و ارزیابی جواب، تأیید، اصلاح، مقادیر بحرانی، کنترل کیفی و ورود از دستگاه."""

import re
from decimal import Decimal, InvalidOperation

import jdatetime
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.audit import log
from apps.audit.documents import DocStatus, DocumentError
from apps.audit.models import AuditLog
from apps.core.db import lock_own_rows
from apps.core.validators import normalize_digits

from ..models import CriticalNotification, LabOrder, LabResult, LabTest, QCMaterial, QCResult, ResultRevision, Specimen


def rules():
    return settings.LAB_RULES


# --- نمونه ---
def create_specimens_and_results(order):
    """یک نمونه با بارکد یکتا برای هر نوع نمونه و یک ردیف جواب برای هر آزمایش."""
    base = order.number.split("-", 1)[1].replace("-", "")
    specimens = {}
    sex, age = order.patient.sex, order.patient.age_days(order.date)
    for item in order.items.select_related("test__specimen_type").prefetch_related("test__reference_ranges"):
        specimen_type = item.test.specimen_type
        if specimen_type.pk not in specimens:
            specimens[specimen_type.pk] = Specimen.objects.create(
                order=order, specimen_type=specimen_type, barcode=f"{base}{len(specimens) + 1:02d}"
            )
        rr = item.test.reference_range_for(sex, age)
        LabResult.objects.create(
            item=item, specimen=specimens[specimen_type.pk], unit=item.test.unit,
            reference_text=rr.display if rr else item.test.normal_text,
        )


def _check_order(order):
    if order.status != DocStatus.POSTED:
        raise DocumentError("پذیرش این نمونه ثبت نهایی نشده یا باطل شده است.")


def collect_specimen(specimen, user):
    _check_order(specimen.order)
    if specimen.status != Specimen.Status.PENDING:
        raise DocumentError("این نمونه قبلاً گرفته شده است.")
    specimen.status = Specimen.Status.COLLECTED
    specimen.collected_at = timezone.now()
    specimen.collected_by = user
    specimen.save(update_fields=["status", "collected_at", "collected_by"])
    update_progress(specimen.order)


def receive_specimen(specimen, user):
    _check_order(specimen.order)
    if specimen.status == Specimen.Status.PENDING:
        collect_specimen(specimen, user)
    if specimen.status != Specimen.Status.COLLECTED:
        raise DocumentError("فقط نمونه گرفته‌شده قابل دریافت در بخش است.")
    specimen.status = Specimen.Status.RECEIVED
    specimen.received_at = timezone.now()
    specimen.received_by = user
    specimen.save(update_fields=["status", "received_at", "received_by"])


@transaction.atomic
def reject_specimen(specimen, reason, user):
    """نمونه نامناسب (همولیز، لخته، حجم کم، ...) مردود و نمونه‌گیری مجدد با بارکد جدید ساخته می‌شود."""
    _check_order(specimen.order)
    if not reason.strip():
        raise DocumentError("علت مردودی نمونه را وارد کنید.")
    if specimen.status == Specimen.Status.REJECTED:
        raise DocumentError("این نمونه قبلاً مردود شده است.")
    if specimen.results.filter(status=LabResult.Status.APPROVED).exists():
        raise DocumentError("جواب تأییدشده روی این نمونه وجود دارد.")
    specimen.status = Specimen.Status.REJECTED
    specimen.rejection_reason = reason.strip()[:200]
    specimen.save(update_fields=["status", "rejection_reason"])
    count = Specimen.objects.filter(order=specimen.order).count()
    base = specimen.barcode[:-2]
    new = Specimen.objects.create(
        order=specimen.order, specimen_type=specimen.specimen_type, barcode=f"{base}{count + 1:02d}", recollect_of=specimen
    )
    for result in specimen.results.all():
        result.specimen = new
        result.status = LabResult.Status.PENDING
        result.value = ""
        result.numeric_value = None
        result.flag = ""
        result.is_critical = False
        result.save()
    log.record(AuditLog.Action.EVENT, specimen, message=f"نمونه مردود: {reason}", user=user)
    update_progress(specimen.order)
    return new


# --- ارزیابی جواب ---
NUMBER_RE = re.compile(r"^\s*([<>]=?)?\s*(-?\d+(?:\.\d+)?)\s*$")


def parse_number(value):
    match = NUMBER_RE.match(normalize_digits(value).replace("٫", ".").replace(",", ""))
    if not match:
        return None
    try:
        return Decimal(match.group(2))
    except InvalidOperation:
        return None


def evaluate(test, value, patient, on_date):
    """خروجی: (مقدار عددی، علامت، بحرانی؟، متن محدوده)."""
    value = (value or "").strip()
    rr = test.reference_range_for(patient.sex, patient.age_days(on_date))
    ref_text = rr.display if rr else test.normal_text
    if test.result_type == LabTest.ResultType.NUMERIC:
        number = parse_number(value)
        if number is None:
            raise DocumentError(f"جواب {test.code} باید عدد باشد.")
        flag, critical = LabResult.Flag.NORMAL, False
        if rr:
            if rr.critical_low is not None and number < rr.critical_low:
                flag, critical = LabResult.Flag.CRITICAL_LOW, True
            elif rr.critical_high is not None and number > rr.critical_high:
                flag, critical = LabResult.Flag.CRITICAL_HIGH, True
            elif rr.low is not None and number < rr.low:
                flag = LabResult.Flag.LOW
            elif rr.high is not None and number > rr.high:
                flag = LabResult.Flag.HIGH
        return number, flag, critical, ref_text
    if test.result_type == LabTest.ResultType.CHOICE and value not in test.choice_list:
        raise DocumentError(f"جواب {test.code} باید یکی از این گزینه‌ها باشد: {'، '.join(test.choice_list)}")
    if not value:
        raise DocumentError(f"جواب {test.code} خالی است.")
    abnormal = bool(test.normal_text) and value != test.normal_text
    return None, (LabResult.Flag.ABNORMAL if abnormal else LabResult.Flag.NORMAL), False, ref_text


def previous_result(result):
    return (
        LabResult.objects.filter(
            item__test=result.item.test, item__order__patient=result.item.order.patient, status=LabResult.Status.APPROVED,
            item__order__status=DocStatus.POSTED,
        )
        .exclude(pk=result.pk).select_related("item__order").order_by("-approved_at").first()
    )


def _delta(result, number):
    test = result.item.test
    if number is None or not test.delta_percent:
        return ""
    prev = previous_result(result)
    if not prev or prev.numeric_value in (None, 0):
        return ""
    change = abs(number - prev.numeric_value) / abs(prev.numeric_value) * 100
    if change < test.delta_percent:
        return ""
    when = jdatetime.date.fromgregorian(date=prev.item.order.date).strftime("%Y/%m/%d")
    return f"تغییر {change:.0f}٪ نسبت به {prev.value} در {when}"


def _editable(result):
    _check_order(result.item.order)
    if result.status == LabResult.Status.APPROVED:
        raise DocumentError("جواب تأییدشده است؛ برای تغییر از «اصلاح جواب» استفاده کنید.")
    if result.specimen and result.specimen.status in (Specimen.Status.PENDING, Specimen.Status.REJECTED):
        raise DocumentError(f"نمونه {result.item.test.code} هنوز گرفته نشده یا مردود است.")


def _apply_value(result, value, comment=None):
    order = result.item.order
    number, flag, critical, ref_text = evaluate(result.item.test, value, order.patient, order.date)
    result.value = value.strip()
    result.numeric_value = number
    result.flag = flag
    result.is_critical = critical
    result.reference_text = ref_text
    result.delta_alert = _delta(result, number)
    if comment is not None:
        result.comment = comment.strip()[:500]


@transaction.atomic
def enter_result(result, value, user, comment=None, instrument=None):
    result = lock_own_rows(LabResult.objects.select_related("item__test", "item__order__patient", "specimen")).get(pk=result.pk)
    _editable(result)
    _apply_value(result, normalize_digits(value) if result.item.test.result_type == LabTest.ResultType.NUMERIC else value, comment)
    result.status = LabResult.Status.ENTERED
    result.entered_by = user
    result.entered_at = timezone.now()
    result.source = LabResult.Source.INSTRUMENT if instrument else LabResult.Source.MANUAL
    result.instrument = instrument
    result.save()
    update_progress(result.item.order)
    return result


def qc_status(test, on_date=None):
    """وضعیت کنترل کیفی امروز آزمایش: None (کنترلی تعریف نشده)، ok، missing یا rejected."""
    on_date = on_date or timezone.localdate()
    materials = list(QCMaterial.objects.filter(test=test, is_active=True))
    if not materials:
        return None
    for material in materials:
        last = material.results.filter(run_at__date=on_date).first()
        if last is None:
            return "missing"
        if last.status == QCResult.Status.REJECTED:
            return "rejected"
    return "ok"


@transaction.atomic
def approve_result(result, user):
    result = lock_own_rows(LabResult.objects.select_related("item__test", "item__order")).get(pk=result.pk)
    _check_order(result.item.order)
    if result.status != LabResult.Status.ENTERED:
        raise DocumentError("فقط جواب ثبت‌شده و تأییدنشده قابل تأیید است.")
    if rules()["approver_must_differ"] and result.entered_by_id == user.pk:
        raise DocumentError("تأییدکننده باید با ثبت‌کننده جواب متفاوت باشد.")
    if result.is_critical and not result.critical_notifications.filter(notified_at__gte=result.entered_at).exists():
        raise DocumentError(f"جواب {result.item.test.code} بحرانی است؛ ابتدا اطلاع‌رسانی به بیمار یا پزشک را ثبت کنید.")
    if rules()["require_qc_for_approval"] and qc_status(result.item.test) in ("missing", "rejected"):
        raise DocumentError(f"کنترل کیفی امروز {result.item.test.code} ثبت نشده یا مردود است.")
    result.status = LabResult.Status.APPROVED
    result.approved_by = user
    result.approved_at = timezone.now()
    result._system_write = True
    result.save()
    log.record(AuditLog.Action.STATUS, result, message=f"تأیید جواب {result.item.test.code} پذیرش {result.item.order.number}", user=user)
    update_progress(result.item.order)
    return result


@transaction.atomic
def amend_result(result, new_value, reason, user, comment=None):
    """اصلاح جواب تأییدشده: نسخه قبلی با علت در تاریخچه می‌ماند و جواب جدید دوباره نیاز به تأیید دارد."""
    if not (reason or "").strip():
        raise DocumentError("علت اصلاح جواب را وارد کنید.")
    result = lock_own_rows(LabResult.objects.select_related("item__test", "item__order__patient")).get(pk=result.pk)
    _check_order(result.item.order)
    if result.status != LabResult.Status.APPROVED:
        raise DocumentError("فقط جواب تأییدشده نیاز به اصلاح دارد؛ جواب تأییدنشده را مستقیم ویرایش کنید.")
    ResultRevision.objects.create(
        result=result, version=result.version, old_value=result.value, old_flag=result.flag,
        old_approved_by=result.approved_by, old_approved_at=result.approved_at, reason=reason.strip()[:300], user=user,
    )
    value = normalize_digits(new_value) if result.item.test.result_type == LabTest.ResultType.NUMERIC else new_value
    _apply_value(result, value, comment)
    result.version += 1
    result.status = LabResult.Status.ENTERED
    result.entered_by = user
    result.entered_at = timezone.now()
    result.approved_by = None
    result.approved_at = None
    result._system_write = True
    result.save()
    log.record(AuditLog.Action.UPDATE, result, message=f"اصلاح جواب: {reason}", user=user)
    update_progress(result.item.order)
    return result


def notify_critical(result, user, recipient, recipient_name, phone, read_back=False, note=""):
    if not result.is_critical:
        raise DocumentError("این جواب بحرانی نیست.")
    if not recipient_name.strip() or not phone.strip():
        raise DocumentError("نام و تلفن گیرنده را وارد کنید.")
    return CriticalNotification.objects.create(
        result=result, recipient=recipient, recipient_name=recipient_name.strip(), phone=normalize_digits(phone),
        read_back=read_back, note=note.strip(), notified_by=user,
    )


def unnotified_critical():
    from django.db.models import Exists, OuterRef

    notified = CriticalNotification.objects.filter(result=OuterRef("pk"), notified_at__gte=OuterRef("entered_at"))
    return (
        LabResult.objects.filter(is_critical=True, item__order__status=DocStatus.POSTED)
        .exclude(status=LabResult.Status.PENDING).exclude(Exists(notified))
        .select_related("item__test", "item__order__patient")
    )


def update_progress(order):
    order = LabOrder.objects.get(pk=order.pk)
    results = list(LabResult.objects.filter(item__order=order).values_list("status", flat=True))
    approved = sum(1 for s in results if s == LabResult.Status.APPROVED)
    collected = order.specimens.exclude(status__in=[Specimen.Status.PENDING, Specimen.Status.REJECTED]).exists()
    if results and approved == len(results):
        progress = LabOrder.Progress.DELIVERED if order.delivered_at else LabOrder.Progress.READY
    elif approved:
        progress = LabOrder.Progress.PARTIAL
    elif collected:
        progress = LabOrder.Progress.IN_PROGRESS
    else:
        progress = LabOrder.Progress.SAMPLING
    if progress != order.progress:
        became_ready = progress == LabOrder.Progress.READY
        order.set_progress(progress)
        if became_ready:
            from . import notify

            notify.result_ready(order)
    return progress


# --- کنترل کیفی (قواعد وست‌گارد) ---
def westgard(z_values):
    """z_values: جدیدترین مقدار اول. خروجی: (وضعیت، قواعد نقض‌شده)."""
    z = z_values[0]
    violations = []
    if abs(z) > 3:
        violations.append("1-3s")
    if len(z_values) >= 2:
        prev = z_values[1]
        if (z > 2 and prev > 2) or (z < -2 and prev < -2):
            violations.append("2-2s")
        if (z > 2 and prev < -2) or (z < -2 and prev > 2):
            violations.append("R-4s")
    if len(z_values) >= 4 and (all(v > 1 for v in z_values[:4]) or all(v < -1 for v in z_values[:4])):
        violations.append("4-1s")
    if len(z_values) >= 10 and (all(v > 0 for v in z_values[:10]) or all(v < 0 for v in z_values[:10])):
        violations.append("10x")
    if violations:
        return QCResult.Status.REJECTED, violations
    if abs(z) > 2:
        return QCResult.Status.WARNING, ["1-2s"]
    return QCResult.Status.ACCEPTED, []


def record_qc(material, value, user, corrective_action=""):
    number = value if isinstance(value, Decimal) else parse_number(str(value))
    if number is None:
        raise DocumentError("مقدار کنترل باید عدد باشد.")
    if material.expiry < timezone.localdate():
        raise DocumentError("ماده کنترل منقضی شده است.")
    z = (number - material.mean) / material.sd
    history = [z] + [r.z_score for r in material.results.all()[:9]]
    status, violations = westgard(history)
    return QCResult.objects.create(
        material=material, value=number, z_score=z.quantize(Decimal("0.01")), status=status,
        violations=", ".join(violations), corrective_action=corrective_action.strip()[:300], user=user,
    )


# --- ورود جواب از دستگاه ---
def import_results(instrument, barcode, rows, user=None):
    """rows: فهرست {"code": کد آزمایش، "value": جواب}. خروجی: (ثبت‌شده‌ها، خطاها)."""
    specimen = Specimen.objects.select_related("order").filter(barcode=normalize_digits(barcode)).first()
    if specimen is None:
        return [], [f"نمونه با بارکد {barcode} پیدا نشد."]
    saved, errors = [], []
    for row in rows:
        code = str(row.get("code", "")).strip().upper()
        result = LabResult.objects.filter(specimen=specimen, item__test__code=code).first()
        if result is None:
            errors.append(f"{code}: برای این نمونه درخواست نشده است.")
            continue
        try:
            enter_result(result, str(row.get("value", "")), user, instrument=instrument)
        except DocumentError as exc:
            errors.append(f"{code}: {' '.join(exc.messages)}")
        else:
            saved.append(code)
    instrument.last_seen_at = timezone.now()
    instrument.save(update_fields=["last_seen_at"])
    return saved, errors

"""قواعد مشاوره آنلاین: ثبت، پرداخت، گفتگو، پایان، رد، لغو، انقضا و استرداد."""

import datetime
import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from apps.audit import log
from apps.audit.models import AuditLog
from apps.core import events
from apps.core.sms import send_sms

from . import payments
from .models import ConsultAttachment, ConsultMessage, ConsultPayment, Consultation

logger = logging.getLogger(__name__)
Status = Consultation.Status


# ---------------------------------------------------------------- دسترسی


def role_for(user, consultation):
    """«patient»، «provider» یا None. کارکنان دیگر (حتی مدیر کل) گفتگو را نمی‌بینند."""
    if not user.is_authenticated:
        return None
    if consultation.patient_id == user.pk:
        return ConsultMessage.Role.PATIENT
    if user.is_staff and Consultation.objects.visible_to_provider(user).filter(pk=consultation.pk).exists():
        return ConsultMessage.Role.PROVIDER
    return None


def log_view(consultation, what="مشاهده گفتگوی مشاوره"):
    log.record(AuditLog.Action.EVENT, consultation, message=what)


# ---------------------------------------------------------------- پیامک (بدون هیچ محتوای پزشکی)


def _url(consultation):
    return settings.SITE_URL + reverse("telemed:detail", args=[consultation.code])


def _mobile(user):
    profile = getattr(user, "profile", None) if user is not None else None
    return getattr(profile, "mobile", None) or ""


def notify_patient(consultation, text):
    mobile = _mobile(consultation.patient)
    if consultation.sms_opt_in and mobile:
        send_sms(mobile, f"{settings.SITE_NAME}: {text}\n{_url(consultation)}", "telemed")


def notify_provider(consultation):
    if consultation.doctor_id and consultation.doctor.user_id:
        mobile = _mobile(consultation.doctor.user)
        if mobile:
            send_sms(mobile, f"{settings.SITE_NAME}: درخواست مشاوره آنلاین جدید دارید.\n{_url(consultation)}", "telemed")


# ---------------------------------------------------------------- ثبت


def free_open_count(user):
    return user.consultations.open().filter(kind=Consultation.Kind.FREE).count()


def _attach(message, files):
    for upload in files or []:
        ConsultAttachment.objects.create(message=message, file=upload)


def create(patient, *, kind, data, doctor=None, scheduled_at=None, files=()):
    """ثبت مشاوره. مشاوره رایگان بلافاصله برای پاسخ ارسال می‌شود؛ مشاوره پولی پس از پرداخت."""
    Kind = Consultation.Kind
    channel = Consultation.Channel.DOCTOR if doctor else Consultation.Channel.PHARMACY
    if doctor is None:
        if kind != Kind.FREE:
            raise ValidationError("مشاوره دارویی فقط به‌صورت پرسش متنی رایگان است.")
        fee = 0
    else:
        profile = getattr(doctor, "online", None)
        if profile is None or not profile.is_enabled or not doctor.is_active:
            raise ValidationError("این پزشک در حال حاضر مشاوره آنلاین ندارد.")
        fee = profile.fee_for(kind)
        if fee is None:
            raise ValidationError("این نوع مشاوره را این پزشک ارائه نمی‌دهد.")
        if kind == Kind.FREE and profile.free_left_today() <= 0:
            raise ValidationError("ظرفیت پرسش رایگان امروز این پزشک تکمیل شده است. فردا دوباره امتحان کنید یا مشاوره پولی را انتخاب کنید.")
        if kind in Consultation.LIVE_KINDS:
            if scheduled_at is None:
                raise ValidationError("زمان ویزیت را انتخاب کنید.")
            local = timezone.localtime(scheduled_at)
            if local.time() not in profile.available_slots(local.date()):
                raise ValidationError("این زمان دیگر آزاد نیست؛ زمان دیگری انتخاب کنید.")
        else:
            scheduled_at = None
    if kind == Kind.FREE and free_open_count(patient) >= settings.TELEMED_FREE_OPEN_PER_USER:
        raise ValidationError("پرسش‌های رایگان باز شما به سقف رسیده است؛ پس از پاسخ و پایان آن‌ها پرسش جدید بفرستید.")

    now = timezone.now()
    data = dict(data)
    description = data.pop("description")
    try:
        with transaction.atomic():
            consultation = Consultation.objects.create(
                patient=patient, channel=channel, doctor=doctor, kind=kind, fee=fee, scheduled_at=scheduled_at,
                status=Status.AWAITING_PAYMENT if fee else Status.WAITING, submitted_at=None if fee else now,
                consent_at=now, **data,
            )
            message = ConsultMessage.objects.create(
                consultation=consultation, sender=patient, role=ConsultMessage.Role.PATIENT, text=description
            )
            _attach(message, files)
    except IntegrityError:
        raise ValidationError("این زمان همین الان رزرو شد؛ زمان دیگری انتخاب کنید.")
    log.record(AuditLog.Action.CREATE, consultation, message="ثبت مشاوره آنلاین")
    if not fee:
        notify_provider(consultation)
    return consultation


# ---------------------------------------------------------------- پرداخت


def _publish_payment(payment, what):
    """رویداد پرداخت برای مصرف‌کننده مالی (صورتحساب، دریافت، استرداد). فقط مبلغ و شناسه‌ها؛ موضوع و متن گفتگو هرگز."""
    consultation = payment.consultation
    patient = consultation.patient
    events.publish(f"telemed.payment.{what}", "telemed.consultation", source_id=consultation.pk, dedupe_key=payment.pk, payload={
        "payment_id": payment.pk, "consultation_id": consultation.pk, "code": consultation.code, "kind": consultation.kind,
        "kind_label": consultation.get_kind_display(), "channel": consultation.channel, "doctor_id": consultation.doctor_id,
        "doctor_name": consultation.provider_name, "patient_user_id": patient.pk,
        "patient_name": patient.get_full_name() or f"کاربر {patient.pk}", "amount": payment.amount, "method": payment.method,
        "gateway": payment.gateway, "reference": payment.reference,
        "refund_reference": getattr(payment, "refund_reference", ""), "at": timezone.now().isoformat(),
    })


def start_payment(consultation, callback_for):
    """callback_for(authority) آدرس کامل بازگشت از درگاه را می‌سازد."""
    if consultation.status != Status.AWAITING_PAYMENT:
        raise ValidationError("این مشاوره در انتظار پرداخت نیست.")
    gateway = payments.gateway()
    if not gateway.online:
        raise ValidationError("پرداخت اینترنتی هنوز فعال نیست؛ برای پرداخت با پذیرش تماس بگیرید.")
    payment = ConsultPayment.objects.create(consultation=consultation, amount=consultation.fee, gateway=gateway.name)
    try:
        return gateway.start(payment, callback_for(str(payment.authority)))
    except payments.GatewayError as exc:
        ConsultPayment.objects.filter(pk=payment.pk).update(status=ConsultPayment.Status.FAILED, note=str(exc)[:300])
        raise ValidationError(str(exc))


def _mark_paid(payment, consultation):
    if consultation.status == Status.AWAITING_PAYMENT:
        consultation.status = Status.WAITING
        consultation.submitted_at = timezone.now()
        consultation.save(update_fields=["status", "submitted_at"])
        notify_provider(consultation)
    else:
        # مثلاً زمان پرداخت تمام شده و مشاوره منقضی شده بود؛ پول بیمار برمی‌گردد.
        payment.status = ConsultPayment.Status.REFUND_DUE
        payment.note = "پرداخت پس از انقضا/لغو مشاوره رسید"
        payment.save(update_fields=["status", "note"])
        _publish_payment(payment, "refund_due")


def complete_payment(authority, request):
    """بازگشت از درگاه. فقط یک بار اثر دارد، حتی اگر بازگشت تکرار شود."""
    with transaction.atomic():
        payment = ConsultPayment.objects.select_for_update().select_related("consultation").get(authority=authority)
        if payment.status != ConsultPayment.Status.PENDING:
            return payment
        ok, reference = payments.GATEWAYS[payment.gateway]().verify(payment, request)
        payment.status = ConsultPayment.Status.PAID if ok else ConsultPayment.Status.FAILED
        payment.reference = reference[:64]
        payment.paid_at = timezone.now() if ok else None
        payment.save()
        if ok:
            _publish_payment(payment, "paid")
            consultation = Consultation.objects.select_for_update().get(pk=payment.consultation_id)
            _mark_paid(payment, consultation)
        else:
            _publish_payment(payment, "failed")
    return payment


def record_manual_payment(consultation, user, reference="", note=""):
    with transaction.atomic():
        consultation = Consultation.objects.select_for_update().get(pk=consultation.pk)
        if consultation.status != Status.AWAITING_PAYMENT:
            raise ValidationError(f"مشاوره {consultation.code} در انتظار پرداخت نیست.")
        payment = ConsultPayment.objects.create(
            consultation=consultation, amount=consultation.fee, method=ConsultPayment.Method.MANUAL,
            status=ConsultPayment.Status.PAID, reference=reference, note=note, recorded_by=user, paid_at=timezone.now(),
        )
        _publish_payment(payment, "paid")
        _mark_paid(payment, consultation)
    return payment


def _refund_due(consultation):
    paid = list(ConsultPayment.objects.filter(consultation=consultation, status=ConsultPayment.Status.PAID).select_related(
        "consultation__patient", "consultation__doctor"))
    ConsultPayment.objects.filter(pk__in=[p.pk for p in paid]).update(status=ConsultPayment.Status.REFUND_DUE)
    for payment in paid:
        _publish_payment(payment, "refund_due")


def mark_refunded(payment, user, reference=""):
    if payment.status != ConsultPayment.Status.REFUND_DUE:
        raise ValidationError("فقط پرداخت «قابل استرداد» را می‌توان مسترد کرد.")
    payment.status = ConsultPayment.Status.REFUNDED
    payment.refunded_at = timezone.now()
    payment.recorded_by = user
    if reference:
        payment.note = f"استرداد: {reference}"[:300]
    with transaction.atomic():
        payment.save()
        payment.refund_reference = reference
        _publish_payment(payment, "refunded")


# ---------------------------------------------------------------- گفتگو


def can_post(consultation, role):
    """(مجاز؟, علت) برای فرستادن پیام."""
    if consultation.status not in Consultation.OPEN_STATUSES:
        return False, "این مشاوره بسته است."
    if role == ConsultMessage.Role.PATIENT and consultation.kind == Consultation.Kind.FREE and consultation.answered_at:
        sent_after = consultation.messages.filter(role=ConsultMessage.Role.PATIENT, created_at__gt=consultation.answered_at).count()
        if sent_after >= settings.TELEMED_FREE_FOLLOWUPS:
            return False, "در پرسش رایگان امکان پرسش تکمیلی بیشتر نیست. برای ادامه، مشاوره جدید یا ویزیت بگیرید."
    if role == ConsultMessage.Role.PATIENT and consultation.answered_at is None:
        pending = consultation.messages.filter(role=ConsultMessage.Role.PATIENT).count()
        if pending >= settings.TELEMED_MAX_MESSAGES_BEFORE_ANSWER:
            return False, "تا پاسخ پزشک صبر کنید."
    return True, ""


def post_message(consultation, user, role, text, files=()):
    with transaction.atomic():
        consultation = Consultation.objects.select_for_update().get(pk=consultation.pk)
        ok, reason = can_post(consultation, role)
        if not ok:
            raise ValidationError(reason)
        if not text.strip() and not files:
            raise ValidationError("متن پیام خالی است.")
        message = ConsultMessage.objects.create(consultation=consultation, sender=user, role=role, text=text.strip())
        _attach(message, files)
        if role == ConsultMessage.Role.PROVIDER:
            first = consultation.answered_at is None
            consultation.status = Status.ANSWERED
            consultation.answered_at = consultation.answered_at or timezone.now()
            consultation.taken_by = consultation.taken_by or user
            consultation.save(update_fields=["status", "answered_at", "taken_by"])
        elif consultation.answered_at:
            consultation.status = Status.WAITING
            consultation.save(update_fields=["status"])
    if role == ConsultMessage.Role.PROVIDER:
        notify_patient(consultation, "پاسخ مشاوره آنلاین شما آماده است." if first else "پیام جدید در مشاوره آنلاین شما.")
    return message


def _system(consultation, text):
    ConsultMessage.objects.create(consultation=consultation, role=ConsultMessage.Role.SYSTEM, text=text)


def _fresh(consultation):
    return Consultation.objects.select_for_update().get(pk=consultation.pk)


@transaction.atomic
def set_meeting_link(consultation, user, link):
    consultation = _fresh(consultation)
    if consultation.kind != Consultation.Kind.VIDEO or not consultation.is_open:
        raise ValidationError("پیوند تماس فقط برای ویزیت تصویری باز ثبت می‌شود.")
    consultation.meeting_link = link
    consultation.taken_by = consultation.taken_by or user
    consultation.save(update_fields=["meeting_link", "taken_by"])
    _system(consultation, "پیوند تماس تصویری ثبت شد.")
    notify_patient(consultation, "پیوند ویزیت تصویری شما ثبت شد.")


@transaction.atomic
def close(consultation, user, outcome):
    consultation = _fresh(consultation)
    if not consultation.is_open:
        raise ValidationError("این مشاوره باز نیست.")
    if consultation.answered_at is None and not consultation.is_live:
        raise ValidationError("پیش از پایان، به بیمار پاسخ دهید یا مشاوره را رد کنید.")
    consultation.status = Status.CLOSED
    consultation.outcome = outcome
    consultation.closed_at = timezone.now()
    consultation.taken_by = consultation.taken_by or user
    consultation.answered_at = consultation.answered_at or consultation.closed_at
    consultation.save()
    _system(consultation, f"مشاوره به پایان رسید: {consultation.get_outcome_display()}.")
    log.record(AuditLog.Action.STATUS, consultation, message="پایان مشاوره")


def decline(consultation, user, reason):
    with transaction.atomic():
        consultation = _fresh(consultation)
        if not consultation.is_open:
            raise ValidationError("این مشاوره باز نیست.")
        consultation.status = Status.DECLINED
        consultation.decline_reason = reason[:300]
        consultation.closed_at = timezone.now()
        consultation.taken_by = consultation.taken_by or user
        consultation.save()
        _refund_due(consultation)
        _system(consultation, f"پاسخ‌دهنده این مشاوره را نپذیرفت: {reason}" + (" مبلغ پرداختی به شما برگردانده می‌شود." if consultation.fee else ""))
    log.record(AuditLog.Action.STATUS, consultation, message="رد مشاوره")
    notify_patient(consultation, "وضعیت مشاوره آنلاین شما تغییر کرد.")


def can_cancel(consultation):
    if consultation.status == Status.AWAITING_PAYMENT:
        return True
    if consultation.status != Status.WAITING or consultation.answered_at:
        return False
    if consultation.is_live:
        return consultation.scheduled_at - timezone.now() >= datetime.timedelta(hours=settings.TELEMED_CANCEL_HOURS)
    return True


def cancel(consultation, user):
    with transaction.atomic():
        consultation = Consultation.objects.select_for_update().get(pk=consultation.pk)
        if not can_cancel(consultation):
            raise ValidationError("این مشاوره دیگر قابل لغو نیست.")
        consultation.status = Status.CANCELLED
        consultation.decline_reason = "لغو توسط بیمار"
        consultation.closed_at = timezone.now()
        consultation.save()
        _refund_due(consultation)
        _system(consultation, "مشاوره توسط بیمار لغو شد." + (" مبلغ پرداختی برگردانده می‌شود." if consultation.paid_payment() else ""))
    log.record(AuditLog.Action.STATUS, consultation, message="لغو مشاوره توسط بیمار")
    return consultation


# ---------------------------------------------------------------- کار زمان‌بندی‌شده


def tick(now=None):
    """انقضای پرداخت‌نشده‌ها و بی‌پاسخ‌ها، پایان خودکار گفتگوهای راکد و یادآوری ویزیت‌های تلفنی/تصویری."""
    now = now or timezone.now()
    stats = {"unpaid_expired": 0, "unanswered_expired": 0, "auto_closed": 0, "reminded": 0}

    unpaid = Consultation.objects.filter(
        status=Status.AWAITING_PAYMENT, created_at__lt=now - datetime.timedelta(minutes=settings.TELEMED_PAYMENT_MINUTES)
    )
    stats["unpaid_expired"] = unpaid.update(status=Status.EXPIRED, closed_at=now)

    for consultation in Consultation.objects.filter(status=Status.WAITING, answered_at__isnull=True).select_related("doctor__online"):
        if consultation.is_live:
            expired = consultation.scheduled_at + datetime.timedelta(hours=settings.TELEMED_LIVE_GRACE_HOURS) < now
        else:
            expired = consultation.response_deadline() < now
        if expired:
            with transaction.atomic():
                Consultation.objects.filter(pk=consultation.pk, status=Status.WAITING).update(status=Status.EXPIRED, closed_at=now)
                _refund_due(consultation)
                _system(consultation, "مهلت پاسخ تمام شد و مشاوره منقضی شد." + (" مبلغ پرداختی برگردانده می‌شود." if consultation.fee else ""))
            notify_patient(consultation, "مشاوره آنلاین شما بدون پاسخ منقضی شد." + (" مبلغ پرداختی برگردانده می‌شود." if consultation.fee else ""))
            stats["unanswered_expired"] += 1

    idle_since = now - datetime.timedelta(days=settings.TELEMED_AUTO_CLOSE_DAYS)
    for consultation in Consultation.objects.filter(status__in=Consultation.OPEN_STATUSES, answered_at__isnull=False):
        last = consultation.messages.order_by("-created_at").values_list("created_at", flat=True).first()
        if last and last < idle_since:
            Consultation.objects.filter(pk=consultation.pk).update(status=Status.CLOSED, closed_at=now, outcome=Consultation.Outcome.ADVICE)
            _system(consultation, "گفتگو به علت عدم فعالیت به‌طور خودکار بسته شد.")
            stats["auto_closed"] += 1

    soon = Consultation.objects.filter(
        status__in=Consultation.OPEN_STATUSES, kind__in=Consultation.LIVE_KINDS, reminded_at__isnull=True,
        scheduled_at__gt=now, scheduled_at__lte=now + datetime.timedelta(minutes=settings.TELEMED_REMINDER_MINUTES),
    )
    for consultation in soon:
        notify_patient(consultation, f"یادآوری: {consultation.get_kind_display()} شما ساعت {timezone.localtime(consultation.scheduled_at):%H:%M} است.")
        Consultation.objects.filter(pk=consultation.pk).update(reminded_at=now)
        stats["reminded"] += 1
    return stats

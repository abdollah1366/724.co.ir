"""گذرگاه رویداد داخلی با الگوی transactional outbox.

تولیدکننده (مثلاً پرونده سلامت) فقط publish را صدا می‌زند؛ رویداد در همان تراکنش داده خودش ذخیره می‌شود. اگر تراکنش
برگردد، رویداد هم نیست؛ اگر مصرف‌کننده (مثلاً مالی) خطا بدهد، داده ماژول درمانی سالم می‌ماند و رویداد در صف با
تلاش مجدد می‌ماند. مصرف‌کننده‌ها در ready() اپ خودشان با subscribe ثبت می‌شوند؛ ماژول تولیدکننده هیچ import ای از
مصرف‌کننده ندارد.

قواعد:
- هر نوع رویداد حداکثر یک handler دارد. handler باید idempotent باشد و در صورت موفقیت متن کوتاه نتیجه
  (مثلاً شماره صورتحساب) برگرداند.
- اگر قابلیت (feature flag) handler خاموش باشد، رویداد «در انتظار» می‌ماند و با روشن شدن دوباره پردازش می‌شود.
- پس از EVENTS_MAX_ATTEMPTS تلاش ناموفق، وضعیت «متوقف» می‌شود و فقط با تلاش مجدد دستی ادامه می‌یابد.
- در لاگ فقط شناسه و نوع رویداد و نوع خطا نوشته می‌شود، نه داده رویداد.
"""

import dataclasses
import datetime
import logging

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Min, Q
from django.utils import timezone

from . import flags
from .middleware import current_request_id
from .models import OutboxEvent

logger = logging.getLogger("apps.core.events")
Status = OutboxEvent.Status


@dataclasses.dataclass(frozen=True)
class Subscription:
    event_type: str
    handler: object
    flag: str | None
    name: str


_subscriptions: dict[str, Subscription] = {}


def subscribe(event_type, flag=None):
    """دکوراتور ثبت handler. flag: کلید قابلیتی که باید روشن باشد تا رویداد پردازش شود."""

    def register(func):
        existing = _subscriptions.get(event_type)
        if existing and existing.handler is not func:
            raise RuntimeError(f"رویداد {event_type} قبلاً handler دارد: {existing.name}")
        _subscriptions[event_type] = Subscription(event_type, func, flag, f"{func.__module__}.{func.__name__}")
        return func

    return register


def subscription(event_type):
    return _subscriptions.get(event_type)


def subscriptions():
    return dict(_subscriptions)


def _source(source, source_id):
    if hasattr(source, "_meta"):
        return source._meta.label_lower, str(source.pk)
    return str(source), str(source_id)


def publish(event_type, source, payload=None, *, source_id=None, dedupe_key="", version=1, occurred_at=None):
    """ذخیره رویداد در صندوق خروجی داخل تراکنش جاری. خروجی: رویداد یا None اگر مصرف‌کننده‌ای ثبت نشده.

    source: نمونه مدل منبع، یا رشته نوع منبع همراه با source_id. انتشار تکراری همان رویداد بی‌اثر است.
    """
    if event_type not in _subscriptions:
        return None
    source_type, source_pk = _source(source, source_id)
    key = {"event_type": event_type, "source_type": source_type, "source_id": source_pk, "dedupe_key": str(dedupe_key)[:64]}
    try:
        with transaction.atomic():
            event = OutboxEvent.objects.create(
                **key, payload=payload or {}, schema_version=version, occurred_at=occurred_at or timezone.now(),
                correlation_id=current_request_id(),
            )
    except IntegrityError:
        return OutboxEvent.objects.get(**key)
    if settings.EVENTS_DISPATCH_INLINE:
        transaction.on_commit(lambda: _dispatch_after_commit(event.pk))
    return event


def _dispatch_after_commit(pk):
    """پردازش فوری پس از commit. هر خطایی اینجا بلعیده می‌شود تا درخواست کاربر ماژول درمانی خراب نشود."""
    try:
        process(pk)
    except Exception:  # noqa: BLE001 - شکست مصرف‌کننده نباید تولیدکننده را متوقف کند
        logger.exception("event_dispatch_crashed event_pk=%s", pk)


def _backoff(attempts):
    minutes = min(2 ** attempts, settings.EVENTS_MAX_BACKOFF_MINUTES)
    return datetime.timedelta(minutes=minutes)


def process(pk, now=None):
    """پردازش یک رویداد با قفل ردیف. خروجی: وضعیت نهایی یا «held» اگر قابلیت خاموش است."""
    now = now or timezone.now()
    with transaction.atomic():
        event = OutboxEvent.objects.select_for_update().filter(pk=pk).first()
        if event is None or event.status not in (Status.PENDING, Status.FAILED):
            return event.status if event else None
        sub = _subscriptions.get(event.event_type)
        if sub is None:
            event.status, event.last_error, event.processed_at = Status.IGNORED, "مصرف‌کننده‌ای ثبت نشده است.", now
            event.save(update_fields=["status", "last_error", "processed_at"])
            return event.status
        if sub.flag and not flags.is_enabled(sub.flag):
            note = f"در انتظار: قابلیت {sub.flag} خاموش است."
            if event.last_error != note:
                event.last_error = note
                event.save(update_fields=["last_error"])
            return "held"
        try:
            with transaction.atomic():
                result = sub.handler(event)
        except Exception as exc:  # noqa: BLE001 - خطای handler ثبت و تلاش مجدد می‌شود
            event.attempts += 1
            dead = event.attempts >= settings.EVENTS_MAX_ATTEMPTS
            event.status = Status.DEAD if dead else Status.FAILED
            event.next_attempt_at = now + _backoff(event.attempts)
            event.last_error = f"{type(exc).__name__}: {exc}"[:500]
            event.save(update_fields=["attempts", "status", "next_attempt_at", "last_error"])
            log = logger.error if dead else logger.warning
            log("event_failed event_id=%s type=%s attempt=%s error=%s correlation=%s", event.event_id, event.event_type,
                event.attempts, type(exc).__name__, event.correlation_id)
            return event.status
        event.status, event.processed_at = Status.PROCESSED, now
        event.result_ref = str(result or "")[:120]
        event.last_error = ""
        event.save(update_fields=["status", "processed_at", "result_ref", "last_error"])
        logger.info("event_processed event_id=%s type=%s result=%s", event.event_id, event.event_type, event.result_ref)
        return event.status


def due(now=None):
    now = now or timezone.now()
    held = [t for t, sub in _subscriptions.items() if sub.flag and not flags.is_enabled(sub.flag)]
    return (
        OutboxEvent.objects.filter(status__in=(Status.PENDING, Status.FAILED), next_attempt_at__lte=now)
        .exclude(event_type__in=held).order_by("id")
    )


def dispatch(limit=500, now=None):
    """پردازش رویدادهای سررسیده به ترتیب ثبت (برای cron: python manage.py dispatch_events)."""
    now = now or timezone.now()
    stats = {"processed": 0, "failed": 0, "dead": 0, "ignored": 0, "held": 0}
    ids = list(due(now).values_list("pk", flat=True)[:limit])
    for pk in ids:
        result = process(pk, now)
        key = {"held": "held", Status.PROCESSED: "processed", Status.FAILED: "failed", Status.DEAD: "dead",
               Status.IGNORED: "ignored"}.get(result)
        if key:
            stats[key] += 1
    return stats


def retry(queryset, now=None):
    """بازگرداندن رویدادهای ناموفق/متوقف به صف (تلاش مجدد دستی و کنترل‌شده)."""
    now = now or timezone.now()
    return queryset.filter(status__in=(Status.FAILED, Status.DEAD)).update(
        status=Status.PENDING, attempts=0, next_attempt_at=now, last_error=""
    )


def health(now=None):
    """خلاصه وضعیت صف برای پایش."""
    now = now or timezone.now()
    counts = dict(OutboxEvent.objects.values_list("status").annotate(n=Count("id")).values_list("status", "n"))
    oldest = OutboxEvent.objects.filter(status__in=(Status.PENDING, Status.FAILED)).aggregate(t=Min("created_at"))["t"]
    held_types = [t for t, sub in _subscriptions.items() if sub.flag and not flags.is_enabled(sub.flag)]
    held = OutboxEvent.objects.filter(status=Status.PENDING, event_type__in=held_types).count() if held_types else 0
    by_type = (
        OutboxEvent.objects.filter(~Q(status__in=(Status.PROCESSED, Status.IGNORED)))
        .values("event_type", "status").annotate(n=Count("id")).order_by("event_type", "status")
    )
    return {
        "pending": counts.get(Status.PENDING, 0), "failed": counts.get(Status.FAILED, 0), "dead": counts.get(Status.DEAD, 0),
        "processed": counts.get(Status.PROCESSED, 0), "held": held,
        "oldest_pending_minutes": int((now - oldest).total_seconds() // 60) if oldest else 0,
        "by_type": list(by_type),
    }

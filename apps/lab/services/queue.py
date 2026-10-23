"""صدور نوبت حضوری، فراخوان به باجه و داده نمایشگر سالن انتظار."""

from django.db import IntegrityError, transaction
from django.db.models import Count, Max
from django.utils import timezone

from apps.audit.documents import DocumentError
from apps.core.validators import normalize_digits

from ..models import QueueCounter, QueueService, QueueTicket, SamplingAppointment

Status = QueueTicket.Status


def issue_ticket(service, priority=QueueTicket.Priority.NORMAL, appointment=None):
    """شماره روزانه هر خدمت از ۱ شروع می‌شود. قفل ردیف خدمت از شماره تکراری در صدور هم‌زمان جلوگیری می‌کند."""
    if not service.is_active:
        raise DocumentError("این خدمت فعلاً نوبت نمی‌دهد.")
    today = timezone.localdate()
    for _ in range(3):
        try:
            with transaction.atomic():
                QueueService.objects.select_for_update().get(pk=service.pk)
                last = QueueTicket.objects.filter(date=today, service=service).aggregate(n=Max("number"))["n"] or 0
                return QueueTicket.objects.create(
                    date=today, service=service, number=last + 1, priority=priority, appointment=appointment,
                )
        except IntegrityError:
            continue
    raise DocumentError("صدور نوبت ناموفق بود؛ دوباره تلاش کنید.")


def check_in(tracking_code, service):
    """کسی که نوبت اینترنتی امروز دارد، با کد پیگیری نوبت حضوری با اولویت می‌گیرد (یک بار)."""
    code = normalize_digits(tracking_code or "").strip().upper()
    appointment = SamplingAppointment.objects.active().filter(
        tracking_code=code, date=timezone.localdate(), kind="lab"
    ).first()
    if appointment is None:
        raise DocumentError("نوبت اینترنتی امروز با این کد پیدا نشد. می‌توانید نوبت عادی بگیرید.")
    existing = appointment.tickets.filter(date=timezone.localdate()).first()
    if existing:
        return existing
    return issue_ticket(service, QueueTicket.Priority.APPOINTMENT, appointment)


def waiting(services, today=None):
    return QueueTicket.objects.filter(
        date=today or timezone.localdate(), service__in=services, status=Status.WAITING
    ).select_related("service").order_by("-priority", "queued_at", "id")


def current_for(counter):
    return QueueTicket.objects.filter(
        date=timezone.localdate(), counter=counter, status=Status.CALLED
    ).select_related("service").order_by("-called_at").first()


def _close_current(counter):
    QueueTicket.objects.filter(date=timezone.localdate(), counter=counter, status=Status.CALLED).update(
        status=Status.DONE, finished_at=timezone.now()
    )


@transaction.atomic
def call_next(counter, user, service=None):
    """نوبت قبلی این باجه را می‌بندد و اولین نوبت در انتظار (اولویت‌دار، سپس نوبت اینترنتی، سپس به ترتیب ورود) را فرا می‌خواند."""
    QueueCounter.objects.select_for_update().get(pk=counter.pk)
    services = [service] if service else counter.services.filter(is_active=True)
    ticket = waiting(services).select_for_update().first()
    _close_current(counter)
    if ticket is None:
        return None
    ticket.status = Status.CALLED
    ticket.counter = counter
    ticket.called_at = timezone.now()
    ticket.called_by = user
    ticket.save(update_fields=["status", "counter", "called_at", "called_by"])
    return ticket


def call_ticket(ticket, counter, user):
    """فراخوان یک شماره مشخص (مثلاً بیماری که حاضر نبود و برگشته است)."""
    if ticket.date != timezone.localdate() or ticket.status not in (Status.WAITING, Status.SKIPPED):
        raise DocumentError("این نوبت قابل فراخوان نیست.")
    with transaction.atomic():
        _close_current(counter)
        ticket.status, ticket.counter, ticket.called_at, ticket.called_by = Status.CALLED, counter, timezone.now(), user
        ticket.save(update_fields=["status", "counter", "called_at", "called_by"])
    return ticket


def _require_called(ticket):
    if ticket.status != Status.CALLED:
        raise DocumentError("این نوبت در حال حاضر فراخوانده نشده است.")


def recall(ticket):
    _require_called(ticket)
    ticket.recall_count += 1
    ticket.called_at = timezone.now()
    ticket.save(update_fields=["recall_count", "called_at"])
    return ticket


def finish(ticket):
    _require_called(ticket)
    ticket.status, ticket.finished_at = Status.DONE, timezone.now()
    ticket.save(update_fields=["status", "finished_at"])


def skip(ticket):
    _require_called(ticket)
    ticket.status, ticket.finished_at = Status.SKIPPED, timezone.now()
    ticket.save(update_fields=["status", "finished_at"])


def transfer(ticket, service):
    """بعد از پذیرش، همان شماره به صف خدمت بعدی (مثلاً نمونه‌گیری) می‌رود؛ شماره بیمار عوض نمی‌شود."""
    if ticket.service_id == service.pk:
        raise DocumentError("نوبت همین حالا در صف این خدمت است.")
    if ticket.status not in (Status.CALLED, Status.WAITING):
        raise DocumentError("این نوبت بسته شده است.")
    ticket.transferred_from = ticket.service
    ticket.service, ticket.status, ticket.counter = service, Status.WAITING, None
    ticket.queued_at, ticket.called_at = timezone.now(), None
    ticket.save(update_fields=["transferred_from", "service", "status", "counter", "queued_at", "called_at"])
    return ticket


def display_data(service_codes=None, history=6):
    """داده نمایشگر: فقط شماره نوبت، نام باجه و تعداد منتظران. هیچ داده شخصی‌ای برگردانده نمی‌شود."""
    today = timezone.localdate()
    services = QueueService.objects.filter(is_active=True)
    if service_codes:
        services = services.filter(code__in=service_codes)
    called = QueueTicket.objects.filter(date=today, service__in=services, called_at__isnull=False).exclude(
        status=Status.WAITING
    ).select_related("service", "counter").order_by("-called_at")[: history + 4]
    rows = [{
        "key": f"{t.pk}:{t.recall_count}:{t.service.code}",
        "ticket": t.label,
        "counter": t.counter.name if t.counter else "",
        "active": t.status == Status.CALLED,
        "at": timezone.localtime(t.called_at).strftime("%H:%M"),
    } for t in called]
    counts = dict(
        QueueTicket.objects.filter(date=today, service__in=services, status=Status.WAITING)
        .values_list("service__code").annotate(n=Count("id"))
    )
    return {
        "time": timezone.localtime().strftime("%H:%M"),
        "calls": rows[:history],
        "waiting": [{"code": s.code, "name": s.name, "count": counts.get(s.code, 0)} for s in services],
    }


def stats(day):
    """میانگین انتظار و تعداد نوبت هر خدمت در یک روز (برای گزارش مدیریت)."""
    rows = []
    for service in QueueService.objects.all():
        tickets = list(QueueTicket.objects.filter(date=day, service=service))
        if not tickets:
            continue
        waits = [t.wait_minutes for t in tickets if t.called_at]
        rows.append({
            "service": service,
            "total": len(tickets),
            "served": sum(t.status == Status.DONE for t in tickets),
            "skipped": sum(t.status == Status.SKIPPED for t in tickets),
            "waiting": sum(t.status == Status.WAITING for t in tickets),
            "avg_wait": round(sum(waits) / len(waits)) if waits else None,
            "max_wait": max(waits) if waits else None,
        })
    return rows

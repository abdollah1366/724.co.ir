"""کیوسک نوبت، کنسول فراخوان باجه و نمایشگر سالن انتظار."""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from ..models import QueueCounter, QueueService, QueueTicket
from ..services import queue
from .staff import _errors, staff_perm

COUNTER_KEY = "lab_queue_counter"


@staff_perm("lab.add_queueticket")
def kiosk(request):
    """صفحه لمسی کنار در ورودی. دستگاه کیوسک با کاربری که فقط نقش «کیوسک نوبت» دارد وارد می‌شود."""
    services = QueueService.objects.filter(is_active=True)
    if request.method == "POST":
        service = get_object_or_404(services, pk=request.POST.get("service"))
        try:
            if request.POST.get("tracking_code"):
                ticket = queue.check_in(request.POST["tracking_code"], service)
            else:
                priority = QueueTicket.Priority.SPECIAL if request.POST.get("special") else QueueTicket.Priority.NORMAL
                ticket = queue.issue_ticket(service, priority)
        except ValidationError as exc:
            _errors(request, exc)
            return redirect("lab:kiosk")
        return redirect("lab:kiosk_ticket", pk=ticket.pk)
    return render(request, "lab/queue/kiosk.html", {"services": services})


@staff_perm("lab.add_queueticket")
def kiosk_ticket(request, pk):
    ticket = get_object_or_404(QueueTicket.objects.select_related("service"), pk=pk, date=timezone.localdate())
    ahead = queue.waiting([ticket.service]).filter(
        Q(priority__gt=ticket.priority) | Q(priority=ticket.priority, queued_at__lt=ticket.queued_at)
    ).count() if ticket.status == QueueTicket.Status.WAITING else 0
    return render(request, "lab/queue/ticket.html", {"ticket": ticket, "ahead": ahead})


def display(request):
    """نمایشگر تمام‌صفحه سالن انتظار. با ?s=A,B فقط صف‌های مشخص نمایش داده می‌شود."""
    return render(request, "lab/queue/display.html", {"codes": request.GET.get("s", "")})


@never_cache
def display_data(request):
    codes = [c for c in request.GET.get("s", "").upper().split(",") if c]
    return JsonResponse(queue.display_data(codes or None), json_dumps_params={"ensure_ascii": False})


def _counter(request):
    counters = QueueCounter.objects.filter(is_active=True).prefetch_related("services")
    if "counter" in request.GET:
        request.session[COUNTER_KEY] = request.GET["counter"]
    pk = request.session.get(COUNTER_KEY)
    return counters, (counters.filter(pk=pk).first() if pk else None)


@staff_perm("lab.change_queueticket")
def console(request):
    counters, counter = _counter(request)
    if counter is None:
        return render(request, "lab/queue/console.html", {"counters": counters})
    services = list(counter.services.filter(is_active=True))
    return render(request, "lab/queue/console.html", {
        "counters": counters, "counter": counter, "current": queue.current_for(counter),
        "waiting": queue.waiting(services)[:30], "skipped": QueueTicket.objects.filter(
            date=timezone.localdate(), service__in=services, status=QueueTicket.Status.SKIPPED
        ).select_related("service").order_by("-finished_at")[:10],
        "services": services, "all_services": QueueService.objects.filter(is_active=True),
    })


@require_POST
@staff_perm("lab.change_queueticket")
def console_action(request):
    _, counter = _counter(request)
    if counter is None:
        return redirect("lab:queue_console")
    action = request.POST.get("action")
    ticket = None
    if request.POST.get("ticket"):
        ticket = get_object_or_404(QueueTicket, pk=request.POST["ticket"], date=timezone.localdate())
    try:
        if action == "next":
            service = counter.services.filter(pk=request.POST.get("service")).first() if request.POST.get("service") else None
            if queue.call_next(counter, request.user, service) is None:
                messages.info(request, "کسی در صف نیست.")
        elif action == "call" and ticket:
            queue.call_ticket(ticket, counter, request.user)
        elif action == "recall" and ticket:
            queue.recall(ticket)
        elif action == "done" and ticket:
            queue.finish(ticket)
        elif action == "skip" and ticket:
            queue.skip(ticket)
        elif action == "transfer" and ticket:
            target = get_object_or_404(QueueService, pk=request.POST.get("service"), is_active=True)
            queue.transfer(ticket, target)
            messages.success(request, f"نوبت {ticket.label} به صف «{target.name}» رفت.")
        elif action == "issue":
            service = get_object_or_404(QueueService, pk=request.POST.get("service"), is_active=True)
            priority = QueueTicket.Priority.SPECIAL if request.POST.get("special") else QueueTicket.Priority.NORMAL
            new = queue.issue_ticket(service, priority)
            messages.success(request, f"نوبت {new.label} صادر شد.")
    except ValidationError as exc:
        _errors(request, exc)
    return redirect("lab:queue_console")

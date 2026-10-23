"""ارتباط با مشتری: ثبت و پیگیری درخواست مشاوره، نظرسنجی و پیامک‌های یادآوری روزانه."""

import calendar
import datetime

from django.conf import settings
from django.db import transaction
from django.db.models import Avg, Count, Q
from django.urls import reverse
from django.utils import timezone

from apps.audit import log
from apps.audit.documents import DocStatus, DocumentError
from apps.audit.models import AuditLog
from apps.core.sms import send_sms

from ..models import (
    ConsultRequest, Feedback, Interaction, LabOrder, LabOrderItem, LabResult, RecallReminder, SamplingAppointment,
)
from . import notify

Status = ConsultRequest.Status


def add_months(day, months):
    month = day.month - 1 + months
    year, month = day.year + month // 12, month % 12 + 1
    return datetime.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def request_received(consult):
    return send_sms(
        consult.mobile,
        f"درخواست مشاوره شما با کد {consult.tracking_code} ثبت شد. کارشناس ما به‌زودی تماس می‌گیرد. {settings.LAB_INFO['name']}",
        "lab_consult",
    )


@transaction.atomic
def log_interaction(consult, kind, text, user, status=None, next_follow_up=None):
    Interaction.objects.create(request=consult, kind=kind, text=text, user=user)
    fields = ["next_follow_up"]
    consult.next_follow_up = next_follow_up
    if consult.assigned_to_id is None:
        consult.assigned_to = user
        fields.append("assigned_to")
    new_status = status or (Status.IN_PROGRESS if consult.status == Status.NEW and kind != Interaction.Kind.NOTE else None)
    if new_status and new_status != consult.status:
        consult.status = new_status
        consult.resolved_at = timezone.now() if new_status in (Status.RESOLVED, Status.CLOSED) else None
        fields += ["status", "resolved_at"]
    consult.save(update_fields=fields)
    return consult


def assign(consult, assignee, user):
    consult.assigned_to = assignee
    consult.save(update_fields=["assigned_to"])
    log.record(AuditLog.Action.UPDATE, consult, message=f"ارجاع به {assignee}", user=user)


def link_order(consult, order):
    consult.order, consult.patient = order, order.patient
    consult.save(update_fields=["order", "patient"])


# --- نظرسنجی ---
def feedback_url(feedback):
    return settings.SITE_URL + reverse("lab:feedback", args=[feedback.token])


@transaction.atomic
def submit_feedback(feedback, data):
    if not feedback.is_open:
        raise DocumentError("مهلت ثبت این نظرسنجی تمام شده یا قبلاً ثبت شده است.")
    for field in ("overall", "waiting", "staff", "sampling", "comment", "wants_contact"):
        setattr(feedback, field, data.get(field))
    feedback.submitted_at = timezone.now()
    feedback.save()
    if feedback.wants_contact or (feedback.overall or 5) <= 2:
        order = feedback.order
        ConsultRequest.objects.create(
            topic=ConsultRequest.Topic.COMPLAINT, channel=ConsultRequest.Channel.FEEDBACK, full_name=order.patient.full_name,
            mobile=order.patient.mobile, patient=order.patient, order=order, message=feedback.comment[:1000],
            consent_at=timezone.now() if feedback.wants_contact else None,
        )
    return feedback


def feedback_summary(since):
    rows = Feedback.objects.filter(submitted_at__date__gte=since)
    summary = rows.aggregate(
        n=Count("id"), avg_overall=Avg("overall"), avg_waiting=Avg("waiting"), avg_staff=Avg("staff"),
        avg_sampling=Avg("sampling"), low=Count("id", filter=Q(overall__lte=2)),
    )
    return {k.removeprefix("avg_"): (round(v, 1) if isinstance(v, float) else v) for k, v in summary.items()}


# --- پیامک‌های روزانه (دستور lab_reminders) ---
def send_appointment_reminders(day):
    """یادآوری نوبت نمونه‌گیری فردا (یک بار برای هر نوبت)."""
    sent = 0
    for appointment in SamplingAppointment.objects.active().filter(date=day, reminder_sent_at__isnull=True).exclude(
        status=SamplingAppointment.Status.ARRIVED
    ):
        notify.appointment_reminder(appointment)
        appointment.reminder_sent_at = timezone.now()
        appointment.save(update_fields=["reminder_sent_at"])
        sent += 1
    return sent


def send_feedback_requests(day):
    """نظرسنجی برای پذیرش‌های یک روز که جوابشان آماده یا تحویل شده است."""
    orders = LabOrder.objects.filter(
        status=DocStatus.POSTED, date=day, progress__in=[LabOrder.Progress.READY, LabOrder.Progress.DELIVERED], feedback__isnull=True
    ).select_related("patient")
    sent = 0
    for order in orders:
        feedback = Feedback.objects.create(order=order, sent_at=timezone.now())
        notify.feedback_request(order, feedback_url(feedback))
        sent += 1
    return sent


def due_recalls(today):
    """ردیف‌هایی که موعد تکرارشان رسیده: جواب تأییدشده، رضایت بیمار به یادآوری، بدون پذیرش جدیدتر همان آزمایش."""
    window = today - datetime.timedelta(days=settings.LAB_RULES["recall_window_days"])
    items = LabOrderItem.objects.filter(
        order__status=DocStatus.POSTED, test__recall_months__gt=0, result__status=LabResult.Status.APPROVED,
        order__patient__reminder_consent_at__isnull=False, recall__isnull=True,
    ).select_related("order__patient", "test")
    due = []
    for item in items:
        due_date = add_months(item.order.date, item.test.recall_months)
        if not (window <= due_date <= today):
            continue
        newer = LabOrderItem.objects.filter(
            order__patient=item.order.patient, test=item.test, order__date__gt=item.order.date
        ).exclude(order__status=DocStatus.REVERSED).exists()
        if not newer:
            due.append((item, due_date))
    return due


@transaction.atomic
def send_recalls(today):
    """برای هر بیمار فقط یک پیامک، بدون نام آزمایش."""
    by_patient = {}
    for item, due_date in due_recalls(today):
        by_patient.setdefault(item.order.patient, []).append((item, due_date))
    for patient, rows in by_patient.items():
        notify.recall_reminder(patient)
        for item, due_date in rows:
            RecallReminder.objects.create(item=item, patient=patient, test=item.test, due_date=due_date)
    return len(by_patient)

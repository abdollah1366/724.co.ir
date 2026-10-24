"""پیامک‌های روزانه آزمایشگاه؛ روزی یک بار (مثلاً ساعت ۱۰ صبح) با cron اجرا شود.

    python manage.py lab_reminders            # یادآوری نوبت فردا، نظرسنجی دیروز، یادآوری آزمایش دوره‌ای
    python manage.py lab_reminders --dry-run  # فقط شمارش، بدون ارسال
"""

import datetime

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from apps.lab.models import LabOrder, SamplingAppointment
from apps.lab.services import crm


class Command(BaseCommand):
    help = "ارسال پیامک‌های یادآوری نوبت، نظرسنجی و آزمایش دوره‌ای"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--no-feedback", action="store_true", help="نظرسنجی پیامکی ارسال نشود")

    def handle(self, *args, dry_run=False, no_feedback=False, **options):
        today = timezone.localdate()
        tomorrow, yesterday = today + datetime.timedelta(days=1), today - datetime.timedelta(days=1)
        if dry_run:
            appointments = SamplingAppointment.objects.active().filter(date=tomorrow, reminder_sent_at__isnull=True).count()
            feedback = LabOrder.objects.filter(
                Q(progress="ready") | Q(progress="delivered"), status="posted", date=yesterday, feedback__isnull=True
            ).count()
            recalls = len({item.order.patient_id for item, _ in crm.due_recalls(today)})
            self.stdout.write(f"نوبت فردا: {appointments} · نظرسنجی: {feedback} · یادآوری دوره‌ای: {recalls} بیمار")
            return
        appointments = crm.send_appointment_reminders(tomorrow)
        feedback = 0 if no_feedback else crm.send_feedback_requests(yesterday)
        recalls = crm.send_recalls(today)
        self.stdout.write(self.style.SUCCESS(f"ارسال شد: نوبت فردا {appointments} · نظرسنجی {feedback} · یادآوری دوره‌ای {recalls}"))

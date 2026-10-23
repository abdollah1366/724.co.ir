"""پیامک‌های روزانه درمانگاه؛ روزی یک بار (مثلاً ساعت ۱۰ صبح) با cron یا Task Scheduler ویندوز اجرا شود.

    python manage.py ehr_reminders            # یادآور ویزیت و دارو (با رضایت بیمار) و یادآوری نوبت‌های فردا
    python manage.py ehr_reminders --dry-run  # فقط شمارش، بدون ارسال

تا انتخاب سرویس پیامک، SMS_BACKEND=console پیامک‌ها را فقط در لاگ و جدول «پیامک‌های ارسالی» ثبت می‌کند.
"""

import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.appointments.models import Appointment
from apps.ehr import engagement


class Command(BaseCommand):
    help = "ارسال پیامک یادآور ویزیت، تمدید دارو و نوبت‌های فردای درمانگاه"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--no-appointments", action="store_true", help="یادآوری نوبت‌های فردا ارسال نشود")

    def handle(self, *args, dry_run=False, no_appointments=False, **options):
        today = timezone.localdate()
        tomorrow = today + datetime.timedelta(days=1)
        if dry_run:
            due = engagement.due_reminders(today)
            consented = due.filter(patient__reminder_consent_at__isnull=False).exclude(patient__mobile="").count()
            appointments = Appointment.objects.active().filter(date=tomorrow, reminder_sent_at__isnull=True).count()
            self.stdout.write(f"یادآور امروز: {due.count()} (با رضایت: {consented}) · نوبت فردا: {appointments}")
            return
        counts = engagement.send_reminders(today)
        appointments = 0 if no_appointments else engagement.send_appointment_reminders(tomorrow)
        self.stdout.write(self.style.SUCCESS(
            f"ارسال شد: یادآور {counts['sent']} · بدون رضایت {counts['no_consent']} · ناموفق {counts['failed']} · نوبت فردا {appointments}"
        ))

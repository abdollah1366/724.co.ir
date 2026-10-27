import datetime

from django.core.management.base import BaseCommand

from apps.clinic.models import Doctor
from apps.telemed.models import DoctorOnlineProfile, OnlineHours


class Command(BaseCommand):
    help = "فعال کردن نمونه مشاوره آنلاین برای پزشکان فعال (فقط برای محیط آزمایشی؛ تعرفه‌ها نمونه است)."

    def add_arguments(self, parser):
        parser.add_argument("--demo", action="store_true", help="تأیید ساخت داده نمونه")

    def handle(self, *args, **options):
        if not options["demo"]:
            self.stderr.write("برای ساخت داده نمونه --demo را بزنید. در محیط واقعی تعرفه‌ها را مدیر در پنل وارد می‌کند.")
            return
        count = 0
        for doctor in Doctor.objects.active():
            profile, created = DoctorOnlineProfile.objects.get_or_create(doctor=doctor, defaults={
                "is_enabled": True, "free_enabled": True, "free_daily_limit": 5, "text_fee": 1_500_000,
                "call_fee": 2_500_000, "video_fee": 3_000_000, "notes": "پاسخ در ساعات کاری همان روز",
            })
            if created and not doctor.online_hours.exists():
                for weekday in (5, 6, 0, 1, 2):  # شنبه تا چهارشنبه
                    OnlineHours.objects.create(doctor=doctor, weekday=weekday, start_time=datetime.time(16),
                                               end_time=datetime.time(19))
                count += 1
        self.stdout.write(f"مشاوره آنلاین برای {count} پزشک فعال شد.")

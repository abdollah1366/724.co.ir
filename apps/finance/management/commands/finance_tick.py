from django.core.management.base import BaseCommand

from apps.finance.services import contracts


class Command(BaseCommand):
    help = "کار روزانه مالی: انقضا و تمدید خودکار قراردادها."

    def handle(self, *args, **options):
        stats = contracts.tick()
        self.stdout.write(f"قراردادهای تمدیدشده: {stats['renewed']}، منقضی‌شده: {stats['expired']}")

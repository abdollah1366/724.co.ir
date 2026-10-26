from django.core.management.base import BaseCommand, CommandError

from apps.finance.reconcile import run_checks


class Command(BaseCommand):
    help = "کنترل‌های مغایرت مالی (تراز، زیردفتر بیماران، کاردکس، رویدادهای معوق). در صورت اختلاف کد خروج ۱."

    def handle(self, *args, **options):
        failed = 0
        for item in run_checks():
            mark = "✓" if item["ok"] else "✗"
            self.stdout.write(f"{mark} {item['name']}: {item['detail']}")
            failed += not item["ok"]
        if failed:
            raise CommandError(f"{failed} کنترل ناموفق")

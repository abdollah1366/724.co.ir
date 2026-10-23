from django.core.management.base import BaseCommand

from apps.inventory import services


class Command(BaseCommand):
    help = "بچ‌های تاریخ‌گذشته را منقضی و از موجودی قابل فروش خارج می‌کند (روزانه اجرا شود)."

    def handle(self, *args, **options):
        count = services.expire_batches()
        self.stdout.write(self.style.SUCCESS(f"{count} بچ منقضی شد."))

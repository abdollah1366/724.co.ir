from django.core.management.base import BaseCommand

from apps.core import events


class Command(BaseCommand):
    help = "پردازش رویدادهای معوق صف خروجی (تلاش مجدد خودکار). روی cron هر ۵ دقیقه."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=500)

    def handle(self, *args, **options):
        stats = events.dispatch(limit=options["limit"])
        self.stdout.write(" ".join(f"{k}={v}" for k, v in stats.items()))

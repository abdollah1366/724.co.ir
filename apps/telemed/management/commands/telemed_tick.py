from django.core.management.base import BaseCommand

from apps.telemed import services


class Command(BaseCommand):
    help = "انقضای مشاوره‌های پرداخت‌نشده و بی‌پاسخ، بستن گفتگوهای راکد و یادآوری ویزیت‌های تلفنی/تصویری (هر ۵ تا ۱۵ دقیقه)."

    def handle(self, *args, **options):
        stats = services.tick()
        self.stdout.write(" ".join(f"{k}={v}" for k, v in stats.items()))

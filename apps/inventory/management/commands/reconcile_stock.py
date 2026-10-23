from django.core.management.base import BaseCommand

from apps.inventory import services


class Command(BaseCommand):
    help = "موجودی قابل فروش کالاها را با دفتر انبار و رزرو سفارش‌ها تطبیق می‌دهد."

    def add_arguments(self, parser):
        parser.add_argument("--fix", action="store_true", help="اصلاح اختلاف‌ها")

    def handle(self, *args, **options):
        diffs = services.reconcile_available(fix=options["fix"])
        for product, current, expected in diffs:
            self.stdout.write(f"{product}: {current} ← {expected}")
        self.stdout.write(self.style.SUCCESS(f"{len(diffs)} اختلاف{' اصلاح شد' if options['fix'] and diffs else ''}."))

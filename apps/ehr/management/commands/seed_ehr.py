from django.core.management.base import BaseCommand

from apps.ehr.icd10_data import COMMON_CODES
from apps.ehr.models import ICD10Code


class Command(BaseCommand):
    help = "ورود فهرست پایه کدهای پرکاربرد ICD-10 (کدهای موجود تغییر نمی‌کنند)."

    def handle(self, *args, **options):
        created = 0
        for code, title, title_fa in COMMON_CODES:
            _, new = ICD10Code.objects.get_or_create(code=code, defaults={"title": title, "title_fa": title_fa})
            created += new
        self.stdout.write(self.style.SUCCESS(f"{created} کد ICD-10 اضافه شد."))

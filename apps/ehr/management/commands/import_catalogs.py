from django.core.management.base import BaseCommand, CommandError

from apps.ehr import importers
from apps.ehr.generic_data import SAMPLE_GENERICS
from apps.ehr.models import GenericDrug


class Command(BaseCommand):
    help = (
        "ورود کاتالوگ‌ها: اکسل ابلاغی کدینگ خدمات سلامت، فهرست داروهای ژنریک (xlsx/csv)، ICD-10 (csv: code,title,title_fa). "
        "مثال: python manage.py import_catalogs --service-codes data/catalogs/service-codes-1404.xlsx "
        "--generics data/catalogs/iran-drug-list.xlsx"
    )

    def add_arguments(self, parser):
        parser.add_argument("--service-codes", help="فایل اکسل کدینگ خدمات سلامت")
        parser.add_argument("--edition", default="1404", help="سال کتاب")
        parser.add_argument("--generics", help="فایل فهرست رسمی داروهای ژنریک")
        parser.add_argument("--sample-generics", action="store_true", help="ورود فهرست نمونه ژنریک (نیازمند تطبیق)")
        parser.add_argument("--icd10", help="فایل CSV کدهای ICD-10")
        parser.add_argument("--link-products", action="store_true", help="اتصال کالاهای داروخانه به ژنریک با نام ژنریک")

    def handle(self, *args, **options):
        if not any(options[k] for k in ("service_codes", "generics", "sample_generics", "icd10", "link_products")):
            raise CommandError("حداقل یکی از گزینه‌ها را بدهید؛ --help را ببینید.")
        try:
            if options["service_codes"]:
                stats = importers.import_service_codes(options["service_codes"], options["edition"])
                self.stdout.write(f"کدینگ خدمات: {stats['created']} جدید، {stats['updated']} به‌روز")
                for sheet, count in stats["sheets"].items():
                    self.stdout.write(f"  {sheet}: {count}")
            if options["generics"]:
                stats = importers.import_generics(options["generics"])
                self.stdout.write(f"داروهای ژنریک: {stats['created']} جدید، {stats['updated']} به‌روز، "
                                  f"{stats['needs_review']} نیازمند بررسی")
                self.stdout.write(f"نام فارسی اضافه‌شده: {importers.fill_persian_names(SAMPLE_GENERICS)}")
            if options["icd10"]:
                stats = importers.import_icd10(options["icd10"])
                self.stdout.write(f"ICD-10: {stats['created']} جدید، {stats['updated']} به‌روز")
        except FileNotFoundError as exc:
            raise CommandError(f"فایل پیدا نشد: {exc.filename}")
        if options["sample_generics"]:
            created = 0
            for name, name_fa, form, strength, route, atc, category, sig in SAMPLE_GENERICS:
                _, new = GenericDrug.objects.get_or_create(
                    name=name, dosage_form=form, strength=strength,
                    defaults={"name_fa": name_fa, "route": route, "atc_code": atc, "category": category, "default_sig": sig,
                              "needs_verification": True},
                )
                created += new
            self.stdout.write(f"ژنریک نمونه: {created} ردیف (نیازمند تطبیق)")
        if options["link_products"] or options["generics"] or options["sample_generics"]:
            self.stdout.write(f"کالاهای متصل‌شده به ژنریک: {importers.link_products_to_generics()}")
        self.stdout.write(self.style.SUCCESS("انجام شد."))

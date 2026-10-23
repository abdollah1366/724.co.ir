"""کاتالوگ پایه آزمایشگاه: بخش‌ها، نوع نمونه، آمادگی، آزمایش‌ها با محدوده مرجع، بسته‌ها و راهنمای دوره‌ای.

    python manage.py seed_lab            # فقط ساختار و آزمایش‌ها؛ تعرفه صفر و راهنماها تأییدنشده
    python manage.py seed_lab --demo     # با تعرفه نمونه و راهنماهای تأییدشده برای آزمایش سامانه

اجرای دوباره چیزی را تکراری نمی‌سازد و مقادیری را که در پنل مدیریت تغییر داده‌اید بازنویسی نمی‌کند.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.lab import catalog_data as data
from apps.lab.models import LabPanel, LabSection, LabTest, PrepInstruction, ReferenceRange, ScreeningRule, SpecimenType


def _dec(value):
    return Decimal(value) if value is not None else None


class Command(BaseCommand):
    help = "ساخت کاتالوگ پایه آزمایشگاه (آزمایش‌ها، آمادگی، محدوده مرجع، بسته‌ها، راهنمای دوره‌ای)."

    def add_arguments(self, parser):
        parser.add_argument("--demo", action="store_true", help="تعرفه نمونه و تأیید راهنماها (فقط برای آزمایش سامانه)")

    @transaction.atomic
    def handle(self, *args, demo=False, **options):
        sections = {
            code: LabSection.objects.get_or_create(code=code, defaults={"name": name, "order": order})[0]
            for code, name, order in data.SECTIONS
        }
        specimens = {
            code: SpecimenType.objects.get_or_create(code=code, defaults={"name": name, "container": container, "color": color})[0]
            for code, name, container, color in data.SPECIMENS
        }
        preps = {
            key: PrepInstruction.objects.get_or_create(title=title, defaults={"text": text, "fasting_hours": hours})[0]
            for key, (title, text, hours) in data.PREPS.items()
        }
        created = 0
        for order, row in enumerate(data.TESTS):
            code, name, name_en, section, specimen, unit, decimals, prep_keys, price, tat, ranges, extra = row
            test, is_new = LabTest.objects.get_or_create(code=code, defaults={
                "name": name, "name_en": name_en, "slug": code.lower(), "section": sections[section],
                "specimen_type": specimens[specimen], "unit": unit, "decimals": decimals, "turnaround_hours": tat,
                "price": price if demo else 0, "order": order, **extra,
            })
            if not is_new:
                continue
            created += 1
            test.preparations.set([preps[k] for k in prep_keys])
            for sex, low, high, crit_low, crit_high in ranges:
                ReferenceRange.objects.create(
                    test=test, sex=sex, low=_dec(low), high=_dec(high), critical_low=_dec(crit_low), critical_high=_dec(crit_high)
                )
        tests = {t.code: t for t in LabTest.objects.all()}
        for code, name, codes, price, description in data.PANELS:
            panel, is_new = LabPanel.objects.get_or_create(code=code, defaults={
                "name": name, "slug": code.lower(), "description": description, "package_price": price if demo else None,
            })
            if is_new:
                panel.tests.set([tests[c] for c in codes])
        for order, (title, sex, age_min, age_max, risk, interval, codes, rationale, source) in enumerate(data.SCREENING):
            rule, is_new = ScreeningRule.objects.get_or_create(title=title, defaults={
                "sex": sex, "age_min": age_min, "age_max": age_max, "risk_factor": risk, "interval": interval,
                "rationale": rationale, "source": source, "is_approved": demo, "order": order,
            })
            if is_new:
                rule.tests.set([tests[c] for c in codes])
        self.stdout.write(self.style.SUCCESS(f"کاتالوگ آزمایشگاه آماده است ({created} آزمایش جدید)."))
        if not demo:
            self.stdout.write(
                "تعرفه‌ها صفر و راهنماهای دوره‌ای «تأییدنشده» ساخته شدند. مسئول فنی باید محدوده‌های مرجع، تعرفه و "
                "راهنماها را در پنل مدیریت بررسی و تأیید کند."
            )

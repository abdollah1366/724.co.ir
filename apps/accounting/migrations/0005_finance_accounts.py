from django.db import migrations

COST_CENTERS = [
    ("PHARM", "داروخانه و تجهیزات"),
    ("LAB", "آزمایشگاه"),
    ("CLINIC", "درمانگاه و ویزیت"),
    ("REHAB", "توانبخشی"),
    ("TELE", "مشاوره و ویزیت آنلاین"),
    ("ADMIN", "اداری و پشتیبانی"),
]

# (کد، نام، ماهیت، نقد/بانک، سیستمی)
ACCOUNTS = [
    ("1107", "مساعده و وام کارکنان", "asset", False, True),
    ("1108", "حساب‌های دریافتنی بیماران (صورتحساب خدمات)", "asset", False, True),
    ("2105", "پیش‌دریافت از بیماران (علی‌الحساب)", "liability", False, True),
    ("2106", "حق‌الزحمه پرداختنی پزشکان و ارائه‌دهندگان", "liability", False, True),
    ("2107", "حقوق و دستمزد پرداختنی", "liability", False, True),
    ("2108", "حق بیمه پرداختنی کارکنان", "liability", False, True),
    ("2109", "مالیات حقوق پرداختنی", "liability", False, True),
    ("3103", "سود و زیان انباشته", "equity", False, True),
    ("4201", "درآمد خدمات درمانگاه و ویزیت", "revenue", False, True),
    ("4202", "درآمد مشاوره و ویزیت آنلاین", "revenue", False, True),
    ("4203", "درآمد خدمات توانبخشی", "revenue", False, True),
    ("4209", "تخفیف خدمات درمانی", "revenue", False, True),
    ("6107", "هزینه حق‌الزحمه پزشکان و ارائه‌دهندگان", "expense", False, True),
    ("6108", "هزینه بیمه سهم کارفرما", "expense", False, False),
]


def create(apps, schema_editor):
    CostCenter = apps.get_model("accounting", "CostCenter")
    Account = apps.get_model("accounting", "Account")
    for code, name in COST_CENTERS:
        CostCenter.objects.get_or_create(code=code, defaults={"name": name})
    for code, name, kind, is_cash, system in ACCOUNTS:
        Account.objects.get_or_create(code=code, defaults={"name": name, "type": kind, "is_cash": is_cash, "is_system": system})


class Migration(migrations.Migration):
    dependencies = [("accounting", "0004_periods_cost_centers_manual_journals")]
    # بازگشت این مهاجرت داده‌ای عمداً بی‌اثر است: حساب‌ها ممکن است آرتیکل داشته باشند و حذف نمی‌شوند.
    operations = [migrations.RunPython(create, migrations.RunPython.noop)]

from django.db import migrations

# (کد، نام، ماهیت، نقد/بانک)
CHART = [
    ("1101", "صندوق", "asset", True),
    ("1102", "بانک", "asset", True),
    ("1103", "حساب‌های دریافتنی مشتریان", "asset", False),
    ("1104", "طلب از سازمان‌های بیمه‌گر", "asset", False),
    ("1105", "موجودی کالا", "asset", False),
    ("1106", "مالیات بر ارزش افزوده خرید", "asset", False),
    ("2101", "حساب‌های پرداختنی تأمین‌کنندگان", "liability", False),
    ("2102", "کالای دریافتی فاکتورنشده", "liability", False),
    ("2103", "مالیات بر ارزش افزوده فروش", "liability", False),
    ("2104", "سایر حساب‌های پرداختنی", "liability", False),
    ("3101", "سرمایه", "equity", False),
    ("3102", "موجودی افتتاحیه (تعدیلات سنواتی)", "equity", False),
    ("4101", "فروش کالا", "revenue", False),
    ("4102", "برگشت از فروش", "revenue", False),
    ("4104", "اضافات انبار", "revenue", False),
    ("5101", "بهای تمام‌شده کالای فروش‌رفته", "expense", False),
    ("5102", "اختلاف قیمت خرید", "expense", False),
    ("5103", "کسری و ضایعات انبار", "expense", False),
    ("5104", "تعدیل بهای موجودی", "expense", False),
    ("6101", "هزینه حقوق و دستمزد", "expense", False),
    ("6102", "هزینه اجاره", "expense", False),
    ("6103", "هزینه آب، برق، گاز و تلفن", "expense", False),
    ("6104", "سایر هزینه‌های عمومی", "expense", False),
    ("6105", "کسری و اضافه صندوق", "expense", False),
]
SYSTEM_EXCEPT = {"3101", "6101", "6102", "6103", "6104"}


def create(apps, schema_editor):
    Account = apps.get_model("accounting", "Account")
    for code, name, type_, is_cash in CHART:
        Account.objects.get_or_create(
            code=code, defaults={"name": name, "type": type_, "is_cash": is_cash, "is_system": code not in SYSTEM_EXCEPT}
        )


class Migration(migrations.Migration):
    dependencies = [("accounting", "0001_initial")]
    operations = [migrations.RunPython(create, migrations.RunPython.noop)]

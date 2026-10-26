from django.db import migrations

# (کد، عنوان، نوع، روش، مشمول مالیات، مشمول بیمه، حساب، حساب بدهی کارفرما، فعال، ترتیب)
# اقلام قانونی (اضافه‌کاری، بیمه، مالیات) بدون نرخ و غیرفعال ساخته می‌شوند؛ نرخ از مقررات معتبر وارد و سپس فعال شود.
# مشمول بیمه/مالیات بودن حقوق پایه پیش‌فرض رایج است و در پنل قابل تغییر است.
ITEMS = [
    ("BASE", "حقوق پایه", "earning", "base", True, True, "6101", None, True, 10),
    ("HOUSING", "حق مسکن", "earning", "profile", False, False, "6101", None, True, 20),
    ("FOOD", "بن خواربار", "earning", "profile", False, False, "6101", None, True, 30),
    ("OVERTIME", "اضافه‌کاری", "earning", "overtime", True, True, "6101", None, False, 40),
    ("BONUS", "پاداش", "earning", "manual", True, False, "6101", None, True, 50),
    ("COMMISSION", "پورسانت", "earning", "manual", True, False, "6101", None, True, 60),
    ("INS_EMP", "بیمه سهم کارمند", "deduction", "pct_ins", False, False, "2108", None, False, 100),
    ("INS_ER", "بیمه سهم کارفرما", "employer", "pct_ins", False, False, "6108", "2108", False, 110),
    ("TAX", "مالیات حقوق", "deduction", "tax", False, False, "2109", None, False, 120),
    ("LOAN", "قسط وام", "deduction", "loan", False, False, "1107", None, True, 130),
    ("ADVANCE", "کسر مساعده", "deduction", "manual", False, False, "1107", None, True, 140),
    ("OTHER_DED", "سایر کسور", "deduction", "manual", False, False, "2104", None, True, 150),
]


def create(apps, schema_editor):
    Account = apps.get_model("accounting", "Account")
    PayItemType = apps.get_model("finance", "PayItemType")
    for code, name, kind, calc, taxable, insurable, account, liability, active, order in ITEMS:
        PayItemType.objects.get_or_create(code=code, defaults={
            "name": name, "kind": kind, "calc": calc, "taxable": taxable, "insurable": insurable,
            "account": Account.objects.get(code=account),
            "liability_account": Account.objects.get(code=liability) if liability else None,
            "is_active": active, "order": order,
        })


class Migration(migrations.Migration):
    dependencies = [("finance", "0001_initial"), ("accounting", "0005_finance_accounts")]
    operations = [migrations.RunPython(create, migrations.RunPython.noop)]

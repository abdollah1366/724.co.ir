from django.db import migrations

ACCOUNTS = [
    ("4105", "درآمد خدمات آزمایشگاه", "revenue"),
    ("4106", "تخفیف خدمات آزمایشگاه", "revenue"),
    ("6106", "کسورات بیمه", "expense"),
]


def create_accounts(apps, schema_editor):
    Account = apps.get_model("accounting", "Account")
    for code, name, kind in ACCOUNTS:
        Account.objects.get_or_create(code=code, defaults={"name": name, "type": kind, "is_system": True})


class Migration(migrations.Migration):
    dependencies = [
        ("lab", "0002_patients_orders_lis"),
        ("accounting", "0002_chart_of_accounts"),
    ]

    operations = [migrations.RunPython(create_accounts, migrations.RunPython.noop)]

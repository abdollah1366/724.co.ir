"""بخش‌های پیش‌فرض انبار و حساب هزینه مصرف داخلی.

نام و تعداد بخش‌ها نمونه است: مدیر می‌تواند بخش‌ها را تغییر نام دهد، غیرفعال کند یا بخش تازه بسازد.
"""

from django.db import migrations

# (کد، نام، نوع، قابل فروش، تأمین از، مرکز هزینه)
SECTIONS = [
    ("DEPOT", "انبار دپو داروخانه", "depot", False, None, "PHARM"),
    ("CLINIC", "انبار بخش کلینیک", "ward", False, "MAIN", "CLINIC"),
    ("PARA", "انبار پاراکلینیک", "ward", False, "MAIN", None),
    ("DRESS", "اتاق پانسمان و تزریقات", "ward", False, "MAIN", "CLINIC"),
]
LOCATIONS = [("GEN", "قفسه عمومی", "room"), ("FRIDGE", "یخچال", "cool")]


def forwards(apps, schema_editor):
    Warehouse = apps.get_model("inventory", "Warehouse")
    Location = apps.get_model("inventory", "Location")
    CostCenter = apps.get_model("accounting", "CostCenter")
    Account = apps.get_model("accounting", "Account")
    Account.objects.get_or_create(code="6110", defaults={"name": "هزینه مصرف دارو و لوازم در بخش‌ها", "type": "expense",
                                                          "is_cash": False, "is_system": True})
    Warehouse.objects.filter(code="MAIN").update(kind="central")
    Warehouse.objects.filter(code="QRN").update(kind="quarantine")
    for code, name, kind, sellable, source, center in SECTIONS:
        warehouse, created = Warehouse.objects.get_or_create(code=code, defaults={"name": name, "kind": kind, "is_sellable": sellable})
        if not created:
            continue
        warehouse.supplies_from = Warehouse.objects.filter(code=source).first() if source else None
        warehouse.cost_center = CostCenter.objects.filter(code=center).first() if center else None
        warehouse.save()
        for loc_code, loc_name, condition in LOCATIONS:
            Location.objects.get_or_create(warehouse=warehouse, code=f"{code}-{loc_code}",
                                           defaults={"name": loc_name, "storage_condition": condition})
    depot = Warehouse.objects.filter(code="DEPOT").first()
    if depot:
        Warehouse.objects.filter(code="MAIN", supplies_from__isnull=True).update(supplies_from=depot)


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0003_warehouse_sections_requests"),
        ("accounting", "0005_finance_accounts"),
    ]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]

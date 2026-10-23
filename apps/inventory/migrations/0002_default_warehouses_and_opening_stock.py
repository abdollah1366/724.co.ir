"""انبارهای پیش‌فرض و انتقال موجودی قبلی کالاها به دفتر انبار.

موجودی قبلی (فیلد stock به‌علاوه رزرو سفارش‌های باز) با یک سند افتتاحیه ثبت‌شده و بچ «LEGACY» بدون
تاریخ انقضا و با بهای صفر وارد می‌شود. داروساز باید با شمارش واقعی و سند اصلاحی، بچ و انقضای واقعی را ثبت کند؛
داشبورد این بچ‌ها را به‌عنوان هشدار نشان می‌دهد.
"""

from django.db import migrations
from django.db.models import Sum

LOCATIONS = [("GEN", "قفسه عمومی", "room"), ("FRIDGE", "یخچال دارویی", "cool"), ("FREEZER", "فریزر", "frozen")]
OPEN_STATUSES = ["pending", "confirmed", "ready"]


def forwards(apps, schema_editor):
    Warehouse = apps.get_model("inventory", "Warehouse")
    Location = apps.get_model("inventory", "Location")
    Batch = apps.get_model("inventory", "Batch")
    StockBalance = apps.get_model("inventory", "StockBalance")
    StockMove = apps.get_model("inventory", "StockMove")
    InventoryDocument = apps.get_model("inventory", "InventoryDocument")
    InventoryDocumentLine = apps.get_model("inventory", "InventoryDocumentLine")
    Product = apps.get_model("shop", "Product")
    OrderItem = apps.get_model("shop", "OrderItem")

    main, _ = Warehouse.objects.get_or_create(code="MAIN", defaults={"name": "انبار اصلی داروخانه", "is_sellable": True})
    qrn, _ = Warehouse.objects.get_or_create(code="QRN", defaults={"name": "انبار قرنطینه و مرجوعی", "is_sellable": False})
    locations = {}
    for warehouse, prefix in ((main, ""), (qrn, "Q-")):
        for code, name, condition in LOCATIONS:
            loc, _ = Location.objects.get_or_create(
                warehouse=warehouse, code=prefix + code, defaults={"name": name, "storage_condition": condition}
            )
            locations[(warehouse.pk, condition)] = loc

    rows = []
    for product in Product.objects.all():
        reserved = (
            OrderItem.objects.filter(product=product, order__status__in=OPEN_STATUSES).aggregate(q=Sum("quantity"))["q"] or 0
        )
        qty = product.stock + reserved
        if qty > 0:
            rows.append((product, qty))
    if not rows:
        return
    doc = InventoryDocument.objects.create(
        number="INV-MIGRATION", status="posted", kind="opening", warehouse=main,
        reason="انتقال موجودی قبلی سامانه به دفتر انبار (بدون بچ و بها؛ نیازمند انبارگردانی)",
    )
    for product, qty in rows:
        number = "LEGACY" if product.track_batch else "-"
        batch, _ = Batch.objects.get_or_create(product=product, number=number)
        location = locations[(main.pk, product.storage_condition)]
        InventoryDocumentLine.objects.create(document=doc, product=product, batch_number=number, location=location,
                                             quantity=qty, unit_cost=0)
        StockBalance.objects.create(product=product, warehouse=main, location=location, batch=batch, quantity=qty)
        StockMove.objects.create(
            move_type="opening", product=product, warehouse=main, location=location, batch=batch, quantity=qty,
            unit_cost=0, total_cost=0, product_qty_after=qty, avg_cost_after=0, source_type="inventory.inventorydocument",
            source_id=doc.pk, source_number=doc.number,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0001_initial"),
        ("shop", "0002_erp_product_fields"),
    ]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]

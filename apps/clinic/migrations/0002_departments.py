# طبقات به «بخش‌های خدماتی» تبدیل می‌شوند؛ داده‌های موجود حفظ می‌شود.
from django.db import migrations, models

KIND_BY_SLUG = {
    "pharmacy": "pharmacy",
    "general-practice": "clinic",
    "specialists": "clinic",
    "laboratory": "lab",
    "cafe": "cafe",
}


def copy_number_to_order(apps, schema_editor):
    Department = apps.get_model("clinic", "Department")
    for dept in Department.objects.all():
        dept.order = max(dept.number, 0) * 10
        dept.kind = KIND_BY_SLUG.get(dept.slug, "other")
        dept.save(update_fields=["order", "kind"])


class Migration(migrations.Migration):
    dependencies = [
        ("clinic", "0001_initial"),
    ]

    operations = [
        migrations.RenameModel("Floor", "Department"),
        migrations.RenameField("service", "floor", "department"),
        migrations.RenameField("doctor", "floor", "department"),
        migrations.AddField(
            model_name="department",
            name="order",
            field=models.PositiveSmallIntegerField(default=0, verbose_name="ترتیب نمایش"),
        ),
        migrations.AddField(
            model_name="department",
            name="kind",
            field=models.CharField(
                choices=[
                    ("pharmacy", "داروخانه"),
                    ("equipment", "تجهیزات پزشکی و بهداشتی"),
                    ("clinic", "درمانگاه و پزشکان"),
                    ("lab", "آزمایشگاه"),
                    ("cafe", "کافی‌شاپ"),
                    ("other", "سایر"),
                ],
                default="other",
                max_length=12,
                verbose_name="نوع بخش",
            ),
        ),
        migrations.RunPython(copy_number_to_order, migrations.RunPython.noop),
        migrations.RemoveField(model_name="department", name="number"),
    ]

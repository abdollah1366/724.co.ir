from django.db import migrations


def seed(apps, schema_editor):
    from apps.ehr.icd10_data import COMMON_CODES

    ICD10Code = apps.get_model("ehr", "ICD10Code")
    for code, title, title_fa in COMMON_CODES:
        ICD10Code.objects.get_or_create(code=code, defaults={"title": title, "title_fa": title_fa})


class Migration(migrations.Migration):
    dependencies = [("ehr", "0003_clinical")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]

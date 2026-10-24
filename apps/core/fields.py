from django.db import models


def unique_when(condition):
    """ستون محاسبه‌شده: ۱ وقتی شرط برقرار است، وگرنه NULL.

    به‌جای UniqueConstraint شرطی (که MySQL/MariaDB پشتیبانی نمی‌کند و بی‌صدا نادیده می‌گیرد) این ستون
    کنار فیلدها در یک UniqueConstraint معمولی می‌آید؛ ردیف‌های NULL محدود نمی‌شوند. روی همه پایگاه‌ها یکسان کار می‌کند.
    """
    return models.GeneratedField(
        expression=models.Case(models.When(condition, then=models.Value(1)), default=None),
        output_field=models.SmallIntegerField(null=True),
        db_persist=True,
    )

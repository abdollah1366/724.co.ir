from django.db import connection


def lock_own_rows(queryset):
    """select_for_update فقط روی جدول خود مدل (نه جدول‌های select_related) وقتی پایگاه داده پشتیبانی کند.

    MariaDB «FOR UPDATE OF» ندارد؛ آنجا ردیف‌های جدول‌های پیوسته هم قفل می‌شوند که فقط کمی محافظه‌کارانه‌تر است.
    """
    if connection.features.has_select_for_update_of:
        return queryset.select_for_update(of=("self",))
    return queryset.select_for_update()

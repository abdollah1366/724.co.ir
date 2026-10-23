from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin

from .models import AuditLog, DocumentSequence

User = get_user_model()


class StaffSafeUserAdmin(UserAdmin):
    """در پنل فنی فقط مدیر ارشد حساب‌ها را تغییر می‌دهد.

    UserAdmin پیش‌فرض به هر کسی که auth.change_user دارد اجازه می‌دهد is_superuser و مجوزها را تیک بزند یا رمز
    هر حسابی را عوض کند. دیگر مدیران از صفحه «کارکنان و نقش‌ها» استفاده می‌کنند که جلوی افزایش دسترسی را می‌گیرد.
    """

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


admin.site.unregister(User)
admin.site.register(User, StaffSafeUserAdmin)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "action", "model", "object_repr", "message", "ip")
    list_filter = ("action", "model")
    search_fields = ("object_repr", "object_id", "message", "user__username")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DocumentSequence)
class DocumentSequenceAdmin(admin.ModelAdmin):
    list_display = ("key", "last_value")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

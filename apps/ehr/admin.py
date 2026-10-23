from django.contrib import admin

from .models import Patient, RecordAccess


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ("file_number", "full_name", "masked_identifier", "mobile", "is_verified", "created_at")
    list_filter = ("is_verified", "sex")
    search_fields = ("file_number", "national_code", "foreign_id", "last_name", "mobile")
    readonly_fields = ("file_number", "created_at", "updated_at")
    raw_id_fields = ("user",)

    def has_delete_permission(self, request, obj=None):
        # پرونده پزشکی حذف نمی‌شود.
        return False


@admin.register(RecordAccess)
class RecordAccessAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "patient", "kind", "reason", "ip")
    list_filter = ("kind",)
    search_fields = ("patient__file_number", "user__username", "reason")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

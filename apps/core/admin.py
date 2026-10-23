from django.contrib import admin

from .models import ContactMessage, SmsMessage

admin.site.site_header = "پنل مدیریت ۷۲۴"
admin.site.site_title = "مدیریت ۷۲۴"
admin.site.index_title = "مدیریت سامانه"


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ("name", "subject", "mobile", "created_at", "is_read")
    list_filter = ("is_read",)
    search_fields = ("name", "subject", "mobile")
    readonly_fields = ("name", "mobile", "subject", "message", "created_at")
    list_editable = ("is_read",)


@admin.register(SmsMessage)
class SmsMessageAdmin(admin.ModelAdmin):
    list_display = ("mobile", "kind", "status", "created_at")
    list_filter = ("kind", "status")
    search_fields = ("mobile",)

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

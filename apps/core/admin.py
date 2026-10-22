from django.contrib import admin

from .models import ContactMessage

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

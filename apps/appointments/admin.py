from django.contrib import admin

from .models import Appointment


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ("tracking_code", "patient_full_name", "doctor", "date", "time", "status")
    list_filter = ("status", "doctor", "date")
    search_fields = ("tracking_code", "patient_full_name", "mobile", "national_code")
    readonly_fields = ("tracking_code", "consent_at", "created_at", "updated_at")
    date_hierarchy = "date"
    list_select_related = ("doctor",)
    actions = ["mark_confirmed", "mark_cancelled"]

    @admin.action(description="تأیید نوبت‌های انتخاب‌شده")
    def mark_confirmed(self, request, queryset):
        queryset.filter(status=Appointment.Status.PENDING).update(status=Appointment.Status.CONFIRMED)

    @admin.action(description="لغو نوبت‌های انتخاب‌شده")
    def mark_cancelled(self, request, queryset):
        queryset.exclude(status=Appointment.Status.DONE).update(status=Appointment.Status.CANCELLED)

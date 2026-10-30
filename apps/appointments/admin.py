from django.contrib import admin

from .models import Appointment


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ("tracking_code", "patient_full_name", "doctor", "date", "time", "status", "source")
    list_filter = ("status", "source", "doctor", "date")
    search_fields = ("tracking_code", "patient_full_name", "mobile", "national_code")
    readonly_fields = ("tracking_code", "consent_at", "created_by", "created_at", "updated_at")
    date_hierarchy = "date"
    list_select_related = ("doctor",)
    raw_id_fields = ("user", "patient")
    actions = ["mark_confirmed", "mark_done", "mark_cancelled"]

    def _set_status(self, queryset, status):
        # ذخیره تک‌تک نوبت‌ها تا سیگنال‌ها (ژتون باشگاه مشتریان) اجرا شوند.
        for appointment in queryset:
            appointment.status = status
            appointment.save(update_fields=["status", "updated_at"])

    @admin.action(description="تأیید نوبت‌های انتخاب‌شده")
    def mark_confirmed(self, request, queryset):
        self._set_status(queryset.filter(status=Appointment.Status.PENDING), Appointment.Status.CONFIRMED)

    @admin.action(description="انجام شد (اعطای ژتون باشگاه)")
    def mark_done(self, request, queryset):
        self._set_status(queryset.exclude(status=Appointment.Status.CANCELLED), Appointment.Status.DONE)

    @admin.action(description="لغو نوبت‌های انتخاب‌شده")
    def mark_cancelled(self, request, queryset):
        self._set_status(queryset.exclude(status=Appointment.Status.DONE), Appointment.Status.CANCELLED)

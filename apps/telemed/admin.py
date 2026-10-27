"""پنل مدیریت مشاوره آنلاین: تنظیم پزشکان، ثبت پرداخت دستی و استرداد.

موضوع و متن گفتگو عمداً اینجا نیست؛ فقط بیمار و پاسخ‌دهنده آن را در صفحه گفتگو می‌بینند.
"""

from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils.html import format_html

from . import services
from .models import ConsultPayment, Consultation, DoctorOnlineProfile, OnlineHours


class OnlineHoursInline(admin.TabularInline):
    model = OnlineHours
    extra = 0
    fk_name = "doctor"


@admin.register(DoctorOnlineProfile)
class DoctorOnlineProfileAdmin(admin.ModelAdmin):
    list_display = ("doctor", "is_enabled", "free_enabled", "free_daily_limit", "text_fee", "call_fee", "video_fee")
    list_filter = ("is_enabled", "free_enabled")
    search_fields = ("doctor__first_name", "doctor__last_name")
    autocomplete_fields = ("doctor",)
    fieldsets = (
        (None, {"fields": ("doctor", "is_enabled", "notes")}),
        ("رایگان", {"fields": ("free_enabled", "free_daily_limit")}),
        ("پولی (تعرفه باید با تعرفه مصوب سازگار باشد)", {"fields": ("text_fee", "call_fee", "video_fee", "slot_minutes")}),
        ("مهلت پاسخ", {"fields": ("response_hours",)}),
    )


@admin.register(OnlineHours)
class OnlineHoursAdmin(admin.ModelAdmin):
    list_display = ("doctor", "weekday", "start_time", "end_time")
    list_filter = ("weekday", "doctor")


class PaymentInline(admin.TabularInline):
    model = ConsultPayment
    extra = 0
    can_delete = False
    fields = ("amount", "method", "gateway", "status", "reference", "paid_at", "refunded_at", "note", "recorded_by")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Consultation)
class ConsultationAdmin(admin.ModelAdmin):
    list_display = ("code", "channel", "doctor", "kind", "status", "fee", "scheduled_at", "created_at")
    list_filter = ("status", "channel", "kind", "doctor")
    search_fields = ("code",)
    date_hierarchy = "created_at"
    inlines = [PaymentInline]
    fields = ("code", "channel", "doctor", "kind", "status", "fee", "scheduled_at", "created_at", "submitted_at",
              "answered_at", "closed_at", "outcome")
    readonly_fields = fields
    actions = ["record_manual_payment"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not request.user.has_perm("telemed.manage_payments"):
            actions.pop("record_manual_payment", None)
        return actions

    @admin.action(description="ثبت پرداخت دستی (کارت‌خوان یا واریز تأییدشده)")
    def record_manual_payment(self, request, queryset):
        done = 0
        for consultation in queryset:
            try:
                services.record_manual_payment(consultation, request.user, note="ثبت از پنل مدیریت")
                done += 1
            except ValidationError as exc:
                self.message_user(request, " ".join(exc.messages), messages.ERROR)
        if done:
            self.message_user(request, f"پرداخت {done} مشاوره ثبت شد.", messages.SUCCESS)


@admin.register(ConsultPayment)
class ConsultPaymentAdmin(admin.ModelAdmin):
    list_display = ("consultation_link", "amount", "method", "gateway", "status", "reference", "paid_at", "refunded_at")
    list_filter = ("status", "method", "gateway")
    search_fields = ("consultation__code", "reference")
    readonly_fields = ("consultation", "amount", "method", "gateway", "authority", "reference", "status", "paid_at",
                       "refunded_at", "recorded_by", "note", "created_at")
    actions = ["mark_refunded"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not request.user.has_perm("telemed.manage_payments"):
            actions.pop("mark_refunded", None)
        return actions

    @admin.display(description="مشاوره")
    def consultation_link(self, obj):
        return format_html('<a href="{}">{}</a>', reverse("admin:telemed_consultation_change", args=[obj.consultation_id]), obj.consultation.code)

    @admin.action(description="استرداد انجام شد")
    def mark_refunded(self, request, queryset):
        done = 0
        for payment in queryset:
            try:
                services.mark_refunded(payment, request.user)
                done += 1
            except ValidationError as exc:
                self.message_user(request, " ".join(exc.messages), messages.ERROR)
        if done:
            self.message_user(request, f"{done} پرداخت مسترد شد.", messages.SUCCESS)

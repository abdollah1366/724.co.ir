from django.contrib import admin, messages

from apps.audit.admin_utils import ReadOnlyAdmin

from .models import (
    ClaimSettlement, CriticalNotification, InsuranceClaim, InsurancePlan, Instrument, LabOrder, LabPanel, LabPatient,
    LabPayment, LabResult, LabSection, LabTest, PlanTestRule, PrepInstruction, QCMaterial, QCResult, ReferenceRange,
    ResultRevision, SamplingAppointment, SamplingSchedule, ScreeningRule, Specimen, SpecimenType,
)


@admin.register(LabSection)
class LabSectionAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "order", "is_active")
    list_editable = ("order", "is_active")


@admin.register(SpecimenType)
class SpecimenTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "container")


@admin.register(PrepInstruction)
class PrepInstructionAdmin(admin.ModelAdmin):
    list_display = ("title", "fasting_hours", "order")


class ReferenceRangeInline(admin.TabularInline):
    model = ReferenceRange
    extra = 0


@admin.register(LabTest)
class LabTestAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "section", "specimen_type", "price", "turnaround_hours", "is_sensitive", "is_public", "is_active")
    list_filter = ("section", "specimen_type", "result_type", "is_sensitive", "is_public", "is_active")
    search_fields = ("name", "name_en", "code", "aliases", "tariff_code")
    prepopulated_fields = {"slug": ("code",)}
    filter_horizontal = ("preparations",)
    inlines = [ReferenceRangeInline]
    fieldsets = (
        (None, {"fields": ("code", "name", "name_en", "slug", "aliases", "section", "specimen_type", "order")}),
        ("اطلاعات برای بیمار", {"fields": ("preparations", "description", "indications", "home_sampling", "is_public")}),
        ("جواب", {"fields": ("result_type", "unit", "decimals", "choices", "normal_text", "delta_percent", "is_sensitive")}),
        ("تعرفه و زمان", {"fields": ("tariff_code", "price", "turnaround_hours", "recall_months", "is_active")}),
    )


@admin.register(LabPanel)
class LabPanelAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "package_price", "is_public", "is_active")
    prepopulated_fields = {"slug": ("code",)}
    filter_horizontal = ("tests",)


@admin.register(ScreeningRule)
class ScreeningRuleAdmin(admin.ModelAdmin):
    list_display = ("title", "sex", "age_min", "age_max", "risk_factor", "interval", "is_approved")
    list_filter = ("is_approved", "sex", "risk_factor")
    filter_horizontal = ("tests",)


# --- بیمه، بیمار و نوبت ---
class PlanTestRuleInline(admin.TabularInline):
    model = PlanTestRule
    extra = 0
    autocomplete_fields = ("test",)


@admin.register(InsurancePlan)
class InsurancePlanAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "insurer", "tariff_percent", "coverage_percent", "max_per_order", "start_date", "end_date", "is_active")
    list_filter = ("kind", "is_active")
    inlines = [PlanTestRuleInline]


@admin.register(LabPatient)
class LabPatientAdmin(admin.ModelAdmin):
    list_display = ("full_name", "national_code", "foreign_id", "mobile", "sex", "birth_date", "user")
    search_fields = ("national_code", "foreign_id", "mobile", "last_name", "first_name")
    readonly_fields = ("user", "created_at", "updated_at", "reminder_consent_at")


@admin.register(SamplingSchedule)
class SamplingScheduleAdmin(admin.ModelAdmin):
    list_display = ("kind", "weekday", "start_time", "end_time", "slot_minutes", "capacity", "is_active")
    list_filter = ("kind", "weekday", "is_active")


@admin.register(SamplingAppointment)
class SamplingAppointmentAdmin(admin.ModelAdmin):
    list_display = ("date", "time", "kind", "full_name", "mobile", "status", "tracking_code")
    list_filter = ("kind", "status", "date")
    search_fields = ("tracking_code", "full_name", "national_code", "mobile")
    readonly_fields = ("tracking_code", "consent_at", "reminder_sent_at", "user")


# --- اسناد (فقط‌خواندنی؛ ثبت و تغییر از پیشخوان آزمایشگاه) ---
@admin.register(LabOrder)
class LabOrderAdmin(ReadOnlyAdmin):
    list_display = ("number", "date", "patient", "status", "progress", "gross_amount", "basic_share", "supplementary_share", "patient_share")
    list_filter = ("status", "progress", "priority", "basic_plan", "supplementary_plan")
    search_fields = ("number", "patient__national_code", "patient__last_name")
    date_hierarchy = "date"


@admin.register(LabPayment)
class LabPaymentAdmin(ReadOnlyAdmin):
    list_display = ("number", "date", "order", "direction", "method", "amount", "status")
    list_filter = ("status", "direction", "method")


@admin.register(InsuranceClaim)
class InsuranceClaimAdmin(ReadOnlyAdmin):
    list_display = ("number", "plan", "period_start", "period_end", "status")


@admin.register(ClaimSettlement)
class ClaimSettlementAdmin(ReadOnlyAdmin):
    list_display = ("number", "claim", "received", "deductions", "status")


@admin.register(Specimen)
class SpecimenAdmin(ReadOnlyAdmin):
    list_display = ("barcode", "order", "specimen_type", "status", "collected_at", "received_at")
    list_filter = ("status", "specimen_type")
    search_fields = ("barcode", "order__number")


@admin.register(LabResult)
class LabResultAdmin(ReadOnlyAdmin):
    list_display = ("item", "value", "flag", "status", "version", "entered_by", "approved_by")
    list_filter = ("status", "flag", "is_critical")
    search_fields = ("item__order__number",)


@admin.register(ResultRevision)
class ResultRevisionAdmin(ReadOnlyAdmin):
    list_display = ("result", "version", "old_value", "reason", "user", "created_at")


@admin.register(CriticalNotification)
class CriticalNotificationAdmin(ReadOnlyAdmin):
    list_display = ("result", "recipient", "recipient_name", "phone", "read_back", "notified_by", "notified_at")


@admin.register(QCMaterial)
class QCMaterialAdmin(admin.ModelAdmin):
    list_display = ("test", "name", "level", "lot", "mean", "sd", "expiry", "is_active")
    list_filter = ("is_active", "test__section")
    autocomplete_fields = ("test",)


@admin.register(QCResult)
class QCResultAdmin(ReadOnlyAdmin):
    list_display = ("material", "value", "z_score", "status", "violations", "user", "run_at")
    list_filter = ("status",)


@admin.register(Instrument)
class InstrumentAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "section", "is_active", "last_seen_at")
    actions = ["generate_key"]

    @admin.action(description="ساخت کلید API جدید (کلید قبلی باطل می‌شود)")
    def generate_key(self, request, queryset):
        for instrument in queryset:
            key = instrument.set_api_key()
            self.message_user(
                request, f"کلید {instrument}: {key} — فقط همین یک بار نمایش داده می‌شود؛ آن را در نرم‌افزار واسط ذخیره کنید.",
                messages.WARNING,
            )

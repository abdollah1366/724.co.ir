from django.contrib import admin

from .models import (
    LabPanel, LabSection, LabTest, PrepInstruction, ReferenceRange, ScreeningRule, SpecimenType,
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

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


from .models import Allergy, Diagnosis, Encounter, EncounterAddendum, HistoryEntry, ICD10Code, Problem, Referral, VitalSigns  # noqa: E402


@admin.register(ICD10Code)
class ICD10CodeAdmin(admin.ModelAdmin):
    list_display = ("code", "title_fa", "title", "is_active")
    search_fields = ("code", "title", "title_fa")
    list_filter = ("is_active",)


class ReadOnlyClinicalAdmin(admin.ModelAdmin):
    """داده بالینی فقط از میز کار پزشک ثبت می‌شود؛ پنل مدیریت فقط برای مشاهده و ممیزی است."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class DiagnosisInline(admin.TabularInline):
    model = Diagnosis
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class AddendumInline(DiagnosisInline):
    model = EncounterAddendum


@admin.register(Encounter)
class EncounterAdmin(ReadOnlyClinicalAdmin):
    list_display = ("started_at", "patient", "provider", "kind", "status")
    list_filter = ("status", "kind", "provider")
    search_fields = ("patient__file_number", "patient__national_code", "patient__last_name")
    inlines = [DiagnosisInline, AddendumInline]


for model in (VitalSigns, Problem, Allergy, HistoryEntry, Referral):
    admin.site.register(model, type(f"{model.__name__}Admin", (ReadOnlyClinicalAdmin,), {
        "list_display": ("__str__", "patient"), "search_fields": ("patient__file_number", "patient__last_name"),
    }))


from .models import EncounterService, GenericDrug, RVUCoefficient, ServiceCode  # noqa: E402


@admin.register(ServiceCode)
class ServiceCodeAdmin(admin.ModelAdmin):
    list_display = ("code", "short_title", "attribute", "total_rvu", "professional_rvu", "technical_rvu", "sheet", "is_active")
    list_filter = ("sheet", "edition", "is_active")
    search_fields = ("code", "title")

    @admin.display(description="شرح")
    def short_title(self, obj):
        return obj.title[:90]


@admin.register(RVUCoefficient)
class RVUCoefficientAdmin(admin.ModelAdmin):
    list_display = ("year", "sector", "component", "value", "reference")
    list_filter = ("year", "sector")


@admin.register(GenericDrug)
class GenericDrugAdmin(admin.ModelAdmin):
    list_display = ("name", "name_fa", "dosage_form", "strength", "code", "category", "needs_verification", "is_active")
    list_filter = ("category", "needs_verification", "is_active", "dosage_form")
    search_fields = ("name", "name_fa", "code", "atc_code")


admin.site.register(EncounterService, type("EncounterServiceAdmin", (ReadOnlyClinicalAdmin,), {
    "list_display": ("encounter", "service_code", "quantity", "unit_tariff"),
}))

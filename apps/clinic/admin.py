from django.contrib import admin

from .models import Department, Doctor, Service, Specialty, WorkingHours


class ServiceInline(admin.TabularInline):
    model = Service
    extra = 0


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("title", "kind", "order", "is_active", "is_coming_soon")
    list_editable = ("order", "is_active", "is_coming_soon")
    list_filter = ("kind",)
    prepopulated_fields = {"slug": ("title",)}
    inlines = [ServiceInline]


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ("title", "department", "price", "requires_qualified_provider", "is_active")
    list_filter = ("department", "is_active", "requires_qualified_provider")
    search_fields = ("title", "description")


@admin.register(Specialty)
class SpecialtyAdmin(admin.ModelAdmin):
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}


class WorkingHoursInline(admin.TabularInline):
    model = WorkingHours
    extra = 1


@admin.register(Doctor)
class DoctorAdmin(admin.ModelAdmin):
    list_display = ("full_name", "specialty", "department", "medical_council_number", "accepts_online_booking", "is_active")
    list_filter = ("department", "specialty", "is_active", "accepts_online_booking")
    search_fields = ("first_name", "last_name", "medical_council_number")
    prepopulated_fields = {"slug": ("first_name", "last_name")}
    raw_id_fields = ("user",)
    inlines = [WorkingHoursInline]

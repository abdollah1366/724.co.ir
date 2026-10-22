from django.contrib import admin

from .models import Doctor, Floor, Service, Specialty, WorkingHours


class ServiceInline(admin.TabularInline):
    model = Service
    extra = 0


@admin.register(Floor)
class FloorAdmin(admin.ModelAdmin):
    list_display = ("title", "number", "is_active", "is_coming_soon")
    list_editable = ("is_active", "is_coming_soon")
    prepopulated_fields = {"slug": ("title",)}
    inlines = [ServiceInline]


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ("title", "floor", "price", "requires_qualified_provider", "is_active")
    list_filter = ("floor", "is_active", "requires_qualified_provider")
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
    list_display = ("full_name", "specialty", "floor", "medical_council_number", "accepts_online_booking", "is_active")
    list_filter = ("floor", "specialty", "is_active", "accepts_online_booking")
    search_fields = ("first_name", "last_name", "medical_council_number")
    prepopulated_fields = {"slug": ("first_name", "last_name")}
    inlines = [WorkingHoursInline]

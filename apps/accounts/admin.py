from django.contrib import admin

from .models import PointTransaction, Profile


class PointTransactionInline(admin.TabularInline):
    model = PointTransaction
    fk_name = "user"
    extra = 0


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("__str__", "mobile", "referral_code", "referred_by", "points", "created_at")
    search_fields = ("mobile", "referral_code", "user__first_name", "user__last_name")
    readonly_fields = ("referral_code", "created_at")
    raw_id_fields = ("user", "referred_by")

    @admin.display(description="ژتون")
    def points(self, obj):
        return obj.points


@admin.register(PointTransaction)
class PointTransactionAdmin(admin.ModelAdmin):
    list_display = ("user", "amount", "reason", "description", "created_at")
    list_filter = ("reason",)
    search_fields = ("user__username", "user__first_name", "user__last_name", "description")
    raw_id_fields = ("user",)

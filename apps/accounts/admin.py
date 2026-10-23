from django.contrib import admin, messages
from django.utils import timezone

from . import club
from .models import PointTransaction, Profile, Redemption, Reward


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


@admin.register(Reward)
class RewardAdmin(admin.ModelAdmin):
    list_display = ("title", "cost", "is_active", "order")
    list_editable = ("cost", "is_active", "order")


@admin.register(Redemption)
class RedemptionAdmin(admin.ModelAdmin):
    list_display = ("code", "user", "reward", "cost", "status", "created_at", "used_at")
    list_filter = ("status", "reward")
    search_fields = ("code", "user__username", "user__first_name", "user__last_name")
    readonly_fields = ("code", "user", "reward", "cost", "status", "created_at", "used_at")
    actions = ["mark_used", "cancel_and_refund"]

    def has_add_permission(self, request):
        return False

    @admin.action(description="استفاده شد")
    def mark_used(self, request, queryset):
        count = queryset.filter(status=Redemption.Status.ISSUED).update(status=Redemption.Status.USED, used_at=timezone.now())
        self.message_user(request, f"{count} جایزه استفاده‌شده علامت خورد.", messages.SUCCESS)

    @admin.action(description="لغو و برگرداندن ژتون")
    def cancel_and_refund(self, request, queryset):
        count = sum(1 for r in queryset.select_related("user") if club.cancel_redemption(r))
        self.message_user(request, f"{count} جایزه لغو و ژتون برگشت داده شد.", messages.SUCCESS)

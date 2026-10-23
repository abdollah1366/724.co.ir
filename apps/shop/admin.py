from django.contrib import admin, messages
from django.utils import timezone

from .models import Category, Order, OrderItem, PharmacyQuestion, Product


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "order")
    list_filter = ("kind",)
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "category", "price", "stock", "requires_prescription", "is_active")
    list_filter = ("kind", "category", "requires_prescription", "is_active")
    list_editable = ("price", "stock", "is_active")
    search_fields = ("name", "generic_name", "brand")
    prepopulated_fields = {"slug": ("name",)}
    fieldsets = (
        (None, {"fields": ("kind", "name", "slug", "category", "brand", "image", "short_description", "description")}),
        ("قیمت و موجودی", {"fields": ("price", "stock", "requires_prescription", "is_active")}),
        (
            "اطلاعات علمی دارو",
            {
                "classes": ("collapse",),
                "fields": (
                    "generic_name", "dosage_form", "strength", "mechanism", "indications", "dosage_note",
                    "side_effects", "warnings", "interactions", "contraindications", "pregnancy_note",
                ),
            },
        ),
        ("مشخصات تجهیزات", {"classes": ("collapse",), "fields": ("specifications", "warranty")}),
    )


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ("product", "product_name", "unit_price", "quantity")
    can_delete = False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("code", "full_name", "mobile", "delivery_method", "total", "status", "created_at")
    list_filter = ("status", "delivery_method")
    search_fields = ("code", "full_name", "mobile")
    readonly_fields = ("code", "user", "total", "created_at", "updated_at")
    inlines = [OrderItemInline]
    actions = ["mark_confirmed", "mark_ready", "mark_delivered", "cancel_orders"]

    def _set_status(self, queryset, status, allowed_from):
        # ذخیره تک‌تک سفارش‌ها تا سیگنال‌ها (مثل ژتون باشگاه مشتریان) اجرا شوند.
        count = 0
        for order in queryset.filter(status__in=allowed_from):
            order.status = status
            order.save(update_fields=["status", "updated_at"])
            count += 1
        return count

    @admin.action(description="تأیید سفارش‌های انتخاب‌شده")
    def mark_confirmed(self, request, queryset):
        self._set_status(queryset, Order.Status.CONFIRMED, [Order.Status.PENDING])

    @admin.action(description="آماده تحویل / ارسال")
    def mark_ready(self, request, queryset):
        self._set_status(queryset, Order.Status.READY, [Order.Status.PENDING, Order.Status.CONFIRMED])

    @admin.action(description="تحویل شد")
    def mark_delivered(self, request, queryset):
        allowed = [Order.Status.PENDING, Order.Status.CONFIRMED, Order.Status.READY]
        self._set_status(queryset, Order.Status.DELIVERED, allowed)

    @admin.action(description="لغو و برگرداندن موجودی")
    def cancel_orders(self, request, queryset):
        count = sum(1 for order in queryset if order.cancel())
        self.message_user(request, f"{count} سفارش لغو شد.", messages.SUCCESS)


@admin.register(PharmacyQuestion)
class PharmacyQuestionAdmin(admin.ModelAdmin):
    list_display = ("name", "mobile", "product", "created_at", "answered_at")
    list_filter = ("answered_at",)
    search_fields = ("name", "mobile", "question")
    readonly_fields = ("user", "name", "mobile", "product", "question", "created_at", "answered_at")

    def save_model(self, request, obj, form, change):
        if obj.answer and not obj.answered_at:
            obj.answered_at = timezone.now()
        super().save_model(request, obj, form, change)

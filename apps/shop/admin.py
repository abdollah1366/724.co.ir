from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import Category, Order, OrderItem, PharmacyQuestion, PriceHistory, Product, ProductPackage, Unit


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    search_fields = ("name",)


class ProductPackageInline(admin.TabularInline):
    model = ProductPackage
    extra = 0


class PriceHistoryInline(admin.TabularInline):
    model = PriceHistory
    extra = 0
    can_delete = False
    readonly_fields = ("price", "previous_price", "changed_by", "changed_at")
    max_num = 0
    verbose_name_plural = "تاریخچه قیمت فروش"


@admin.register(PriceHistory)
class PriceHistoryAdmin(admin.ModelAdmin):
    list_display = ("product", "previous_price", "price", "changed_by", "changed_at")
    search_fields = ("product__name",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "order")
    list_filter = ("kind",)
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "sku", "kind", "category", "price", "stock", "avg_cost", "requires_prescription", "is_active")
    list_filter = ("kind", "category", "requires_prescription", "is_controlled", "storage_condition", "is_sellable", "is_active")
    # موجودی فقط با اسناد انبار، خرید و فروش تغییر می‌کند و اینجا قابل ویرایش نیست.
    list_editable = ("price", "is_active")
    search_fields = ("name", "generic_name", "brand", "sku", "irc", "packages__barcode")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("stock", "avg_cost")
    autocomplete_fields = ("generic",)
    inlines = [ProductPackageInline, PriceHistoryInline]
    fieldsets = (
        (None, {"fields": ("kind", "name", "slug", "category", "brand", "image", "short_description", "description")}),
        ("قیمت و موجودی", {"fields": ("price", "tax_rate", "stock", "avg_cost", "reorder_point", "is_sellable", "is_active")}),
        ("کدها و واحد", {"fields": ("sku", "base_unit", "irc", "imed_code", "device_class")}),
        (
            "ردیابی و محدودیت فروش",
            {
                "fields": (
                    "storage_condition", "track_batch", "track_expiry", "track_serial", "requires_authenticity_check",
                    "requires_prescription", "is_controlled", "max_qty_per_sale",
                )
            },
        ),
        (
            "اطلاعات علمی دارو",
            {
                "classes": ("collapse",),
                "fields": (
                    "generic", "generic_name", "dosage_form", "strength", "mechanism", "indications", "dosage_note",
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


class OrderAdminForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = "__all__"

    def clean_status(self):
        status = self.cleaned_data["status"]
        old = self.instance.status if self.instance.pk else None
        # لغو و تحویل اثر انبار و مالی دارند و فقط از اکشن‌های فهرست سفارش‌ها انجام می‌شوند.
        if status != old and status in (Order.Status.CANCELLED, Order.Status.DELIVERED):
            raise forms.ValidationError("برای لغو یا تحویل، از اکشن‌های فهرست سفارش‌ها استفاده کنید.")
        if old in (Order.Status.CANCELLED, Order.Status.DELIVERED) and status != old:
            raise forms.ValidationError("وضعیت سفارش لغوشده یا تحویل‌شده قابل تغییر نیست.")
        return status


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    form = OrderAdminForm
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

    @admin.action(description="تحویل شد (صدور فاکتور فروش و خروج از انبار)")
    def mark_delivered(self, request, queryset):
        allowed = [Order.Status.PENDING, Order.Status.CONFIRMED, Order.Status.READY]
        for order in queryset.filter(status__in=allowed):
            try:
                self._set_status(Order.objects.filter(pk=order.pk), Order.Status.DELIVERED, allowed)
            except ValidationError as exc:
                self.message_user(request, f"سفارش {order.code}: {' '.join(exc.messages)}", messages.ERROR)

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

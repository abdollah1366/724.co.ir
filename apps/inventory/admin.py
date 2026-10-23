from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import render

from apps.audit.admin_utils import DocumentAdmin, LockedInlineMixin, ReadOnlyAdmin

from . import services
from .models import Batch, BatchStatusChange, InventoryDocument, InventoryDocumentLine, Location, StockBalance, StockMove, Warehouse


class LocationInline(admin.TabularInline):
    model = Location
    extra = 0


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_sellable", "is_active")
    inlines = [LocationInline]


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ("warehouse", "code", "name", "storage_condition", "is_active")
    list_filter = ("warehouse", "storage_condition")


class BatchStatusForm(forms.Form):
    status = forms.ChoiceField(label="وضعیت جدید", choices=Batch.Status.choices)
    reason = forms.CharField(label="علت", max_length=300)
    reference = forms.CharField(label="شماره بخشنامه / مرجع", max_length=60, required=False)


class StatusChangeInline(admin.TabularInline):
    model = BatchStatusChange
    extra = 0
    can_delete = False
    max_num = 0
    readonly_fields = ("created_at", "old_status", "new_status", "reason", "reference", "user")


class BalanceInline(admin.TabularInline):
    model = StockBalance
    extra = 0
    can_delete = False
    max_num = 0
    readonly_fields = ("warehouse", "location", "quantity")
    fields = readonly_fields


@admin.register(Batch)
class BatchAdmin(admin.ModelAdmin):
    list_display = ("product", "number", "expiry_date", "status", "on_hand")
    list_filter = ("status", "product__kind")
    search_fields = ("number", "product__name")
    # بچ فقط با رسید یا سند انبار ساخته و فقط با اکشن «تغییر وضعیت» تغییر می‌کند (انقضا قابل ویرایش دستی نیست).
    readonly_fields = ("product", "number", "expiry_date", "manufacture_date", "status", "created_at")
    inlines = [BalanceInline, StatusChangeInline]
    actions = ["change_status"]

    def has_add_permission(self, request):
        return False  # بچ فقط با رسید یا سند انبار ساخته می‌شود

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="مانده")
    def on_hand(self, obj):
        return sum(b.quantity for b in obj.balances.all())

    @admin.action(description="تغییر وضعیت (قرنطینه، فراخوان، توقیف، آزادسازی)")
    def change_status(self, request, queryset):
        if not request.user.has_perm("inventory.change_batch_status"):
            raise PermissionDenied
        if "apply" in request.POST:
            form = BatchStatusForm(request.POST)
            if form.is_valid():
                for batch in queryset:
                    try:
                        services.change_batch_status(batch, form.cleaned_data["status"], form.cleaned_data["reason"],
                                                     request.user, form.cleaned_data["reference"])
                    except ValidationError as exc:
                        self.message_user(request, f"{batch}: {' '.join(exc.messages)}", messages.ERROR)
                self.message_user(request, "وضعیت بچ‌ها به‌روز شد.", messages.SUCCESS)
                return None
        else:
            form = BatchStatusForm()
        return render(request, "admin/erp/batch_status.html", {
            **self.admin_site.each_context(request), "form": form, "batches": queryset, "opts": self.model._meta,
            "title": "تغییر وضعیت بچ",
        })


@admin.register(StockBalance)
class StockBalanceAdmin(ReadOnlyAdmin):
    list_display = ("product", "warehouse", "location", "batch", "expiry", "quantity")
    list_filter = ("warehouse", "batch__status", "product__kind")
    search_fields = ("product__name", "batch__number")

    @admin.display(description="انقضا")
    def expiry(self, obj):
        return obj.batch.expiry_date

    def get_queryset(self, request):
        return super().get_queryset(request).filter(quantity__gt=0).select_related("product", "warehouse", "location", "batch")


@admin.register(StockMove)
class StockMoveAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "move_type", "is_reversal", "product", "warehouse", "batch", "quantity", "unit_cost",
                    "total_cost", "product_qty_after", "avg_cost_after", "source_number", "user")
    list_filter = ("move_type", "is_reversal", "warehouse")
    search_fields = ("product__name", "source_number", "batch__number")


@admin.register(BatchStatusChange)
class BatchStatusChangeAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "batch", "old_status", "new_status", "reason", "reference", "user")
    list_filter = ("new_status",)


class InventoryLineInline(LockedInlineMixin, admin.TabularInline):
    model = InventoryDocumentLine
    autocomplete_fields = ("product",)


@admin.register(InventoryDocument)
class InventoryDocumentAdmin(DocumentAdmin):
    list_display = ("__str__", "kind", "date", "warehouse", "status", "posted_by")
    list_filter = ("status", "kind", "warehouse")
    search_fields = ("number", "reason")
    inlines = [InventoryLineInline]

from django.contrib import admin

from apps.audit.admin_utils import DocumentAdmin, LockedInlineMixin, ReadOnlyAdmin

from .models import CashRegister, CashSession, SalesAllocation, SalesInvoice, SalesInvoiceLine, SalesPayment, SalesReturn, SalesReturnLine


@admin.register(CashRegister)
class CashRegisterAdmin(admin.ModelAdmin):
    list_display = ("name", "department", "warehouse", "cash_account", "card_account", "is_active")
    list_filter = ("department", "is_active")
    search_fields = ("name",)
    filter_horizontal = ("cashiers",)
    fields = ("name", "department", "warehouse", "cash_account", "card_account", "cashiers", "is_active")

    def has_delete_permission(self, request, obj=None):
        # صندوقی که شیفت داشته حذف نمی‌شود (سابقه مالی)؛ غیرفعال کنید.
        if obj is not None and obj.sessions.exists():
            return False
        return super().has_delete_permission(request, obj)


@admin.register(CashSession)
class CashSessionAdmin(ReadOnlyAdmin):
    list_display = ("register", "cashier", "status", "opened_at", "opening_cash", "closed_at", "expected_cash",
                    "counted_cash", "difference")
    list_filter = ("status", "register")

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("sales.view_cashsession")


class SalesLineInline(LockedInlineMixin, admin.TabularInline):
    model = SalesInvoiceLine
    autocomplete_fields = ("product",)
    readonly_fields = ("tax", "cost_total")


class SalesPaymentInline(LockedInlineMixin, admin.TabularInline):
    model = SalesPayment


@admin.register(SalesInvoice)
class SalesInvoiceAdmin(DocumentAdmin):
    list_display = ("__str__", "channel", "date", "customer", "total", "insurance_amount", "status", "posted_by")
    list_filter = ("status", "channel")
    search_fields = ("number", "prescription_number", "customer__name", "order__code")
    inlines = [SalesLineInline, SalesPaymentInline]
    doc_readonly = DocumentAdmin.doc_readonly + ("pharmacist_approved_by", "order")


@admin.register(SalesAllocation)
class SalesAllocationAdmin(ReadOnlyAdmin):
    list_display = ("line", "batch", "location", "quantity", "cost_total", "returned_quantity")
    search_fields = ("batch__number", "line__product__name", "line__document__number")


class ReturnLineInline(LockedInlineMixin, admin.TabularInline):
    model = SalesReturnLine


@admin.register(SalesReturn)
class SalesReturnAdmin(DocumentAdmin):
    list_display = ("__str__", "invoice", "date", "restock", "refund_method", "refund_amount", "status")
    search_fields = ("number", "invoice__number")
    inlines = [ReturnLineInline]
    raw_id_fields = ("invoice",)

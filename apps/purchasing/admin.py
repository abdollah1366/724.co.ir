from django.contrib import admin, messages

from apps.audit.admin_utils import DocumentAdmin, LockedInlineMixin
from apps.audit.documents import DocStatus

from . import services
from .models import GoodsReceipt, GoodsReceiptLine, PurchaseInvoice, PurchaseInvoiceLine, PurchaseOrder, PurchaseOrderLine


class POLineInline(LockedInlineMixin, admin.TabularInline):
    model = PurchaseOrderLine
    autocomplete_fields = ("product",)


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(DocumentAdmin):
    list_display = ("__str__", "supplier", "date", "status", "total")
    search_fields = ("number", "supplier__name")
    inlines = [POLineInline]


class ReceiptLineInline(LockedInlineMixin, admin.TabularInline):
    model = GoodsReceiptLine
    autocomplete_fields = ("product",)
    fields = ("product", "po_line", "batch_number", "manufacture_date", "expiry_date", "location", "quantity", "unit_cost",
              "authenticity_code", "authenticity_verified")


@admin.register(GoodsReceipt)
class GoodsReceiptAdmin(DocumentAdmin):
    list_display = ("__str__", "supplier", "purchase_order", "warehouse", "date", "status", "total")
    search_fields = ("number", "supplier__name", "delivery_note")
    inlines = [ReceiptLineInline]


class InvoiceLineInline(LockedInlineMixin, admin.TabularInline):
    model = PurchaseInvoiceLine


@admin.register(PurchaseInvoice)
class PurchaseInvoiceAdmin(DocumentAdmin):
    list_display = ("__str__", "supplier", "supplier_invoice_no", "receipt", "date", "status", "total")
    search_fields = ("number", "supplier_invoice_no", "supplier__name")
    inlines = [InvoiceLineInline]
    actions = ["post_selected", "fill_from_receipt"]

    @admin.action(description="پر کردن ردیف‌ها از رسید انبار")
    def fill_from_receipt(self, request, queryset):
        for invoice in queryset.filter(status=DocStatus.DRAFT):
            services.fill_invoice_from_receipt(invoice)
        self.message_user(request, "ردیف‌ها از رسید پر شد.", messages.SUCCESS)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        if form.instance.status == DocStatus.DRAFT and not form.instance.lines.exists():
            services.fill_invoice_from_receipt(form.instance)

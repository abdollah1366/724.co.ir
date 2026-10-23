from django.contrib import admin

from apps.audit.admin_utils import DocumentAdmin, ReadOnlyAdmin

from .models import Account, Expense, JournalEntry, JournalLine, Party, Payment


@admin.register(Party)
class PartyAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "code", "national_id", "phone", "is_active")
    list_filter = ("kind", "is_active")
    search_fields = ("name", "code", "national_id", "mobile")
    raw_id_fields = ("user",)


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "type", "is_cash", "is_system", "is_active")
    list_filter = ("type",)
    search_fields = ("code", "name")

    def get_readonly_fields(self, request, obj=None):
        return ("code", "type", "is_system") if obj and obj.is_system else ("is_system",)

    def has_delete_permission(self, request, obj=None):
        return False


class JournalLineInline(admin.TabularInline):
    model = JournalLine
    extra = 0
    can_delete = False
    max_num = 0
    readonly_fields = ("account", "party", "debit", "credit", "description")


@admin.register(JournalEntry)
class JournalEntryAdmin(ReadOnlyAdmin):
    list_display = ("number", "date", "description", "source_number", "is_reversal", "created_by")
    list_filter = ("is_reversal", "source_type")
    search_fields = ("number", "description", "source_number")
    date_hierarchy = "date"
    inlines = [JournalLineInline]

    def has_change_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return super().has_view_permission(request, obj)


@admin.register(Payment)
class PaymentAdmin(DocumentAdmin):
    list_display = ("__str__", "direction", "party", "method", "amount", "reference_no", "date", "status")
    list_filter = ("status", "direction", "method")
    search_fields = ("number", "party__name", "reference_no")


@admin.register(Expense)
class ExpenseAdmin(DocumentAdmin):
    list_display = ("__str__", "account", "amount", "method", "party", "date", "status")
    list_filter = ("status", "account")
    search_fields = ("number", "description")

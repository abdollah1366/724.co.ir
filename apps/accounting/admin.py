from django.contrib import admin

from django.contrib import messages
from django.core.exceptions import ValidationError

from apps.audit.admin_utils import DocumentAdmin, LockedInlineMixin, ReadOnlyAdmin

from . import services
from .models import (
    AccountingPeriod, Account, BankReconciliation, CostCenter, Expense, JournalEntry, JournalLine, ManualJournal,
    ManualJournalLine, Party, Payment,
)


@admin.register(Party)
class PartyAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "code", "national_id", "phone", "is_active")
    list_filter = ("kind", "is_active")
    search_fields = ("name", "code", "national_id", "mobile")
    raw_id_fields = ("user",)


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "type", "is_cash", "bank_name", "is_system", "is_active")
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
    readonly_fields = ("account", "party", "cost_center", "debit", "credit", "description")


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


@admin.register(CostCenter)
class CostCenterAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active")

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AccountingPeriod)
class AccountingPeriodAdmin(admin.ModelAdmin):
    list_display = ("name", "start_date", "end_date", "is_year_end", "status", "closed_by", "closed_at")
    readonly_fields = ("status", "closed_by", "closed_at", "note")
    actions = ["close_selected", "reopen_selected"]

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.status == AccountingPeriod.Status.CLOSED:
            return [f.name for f in self.model._meta.fields]
        return super().get_readonly_fields(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False

    def _can_close(self, request):
        return request.user.has_perm("accounting.close_accountingperiod")

    @admin.action(description="بستن دوره‌های انتخاب‌شده")
    def close_selected(self, request, queryset):
        if not self._can_close(request):
            self.message_user(request, "مجوز بستن دوره ندارید.", messages.ERROR)
            return
        for period in queryset.order_by("start_date"):
            try:
                services.close_period(period, request.user, "بستن از پنل")
                self.message_user(request, f"دوره {period} بسته شد.", messages.SUCCESS)
            except ValidationError as exc:
                self.message_user(request, f"{period}: {' '.join(exc.messages)}", messages.ERROR)

    @admin.action(description="بازگشایی دوره‌های انتخاب‌شده (با ثبت در گزارش تغییرات)")
    def reopen_selected(self, request, queryset):
        if not self._can_close(request):
            self.message_user(request, "مجوز بازگشایی دوره ندارید.", messages.ERROR)
            return
        for period in queryset:
            try:
                services.reopen_period(period, request.user, "بازگشایی از پنل")
                self.message_user(request, f"دوره {period} باز شد.", messages.WARNING)
            except ValidationError as exc:
                self.message_user(request, f"{period}: {' '.join(exc.messages)}", messages.ERROR)


class ManualJournalLineInline(LockedInlineMixin, admin.TabularInline):
    model = ManualJournalLine
    fields = ("account", "party", "cost_center", "debit", "credit", "description")
    extra = 2


@admin.register(ManualJournal)
class ManualJournalAdmin(DocumentAdmin):
    list_display = ("__str__", "date", "kind", "description", "status")
    list_filter = ("status", "kind")
    search_fields = ("number", "description")
    inlines = [ManualJournalLineInline]


@admin.register(BankReconciliation)
class BankReconciliationAdmin(admin.ModelAdmin):
    list_display = ("account", "statement_date", "statement_balance", "status", "finalized_by")
    readonly_fields = ("status", "created_by", "finalized_by", "finalized_at")

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        return obj is None or obj.status == BankReconciliation.Status.DRAFT

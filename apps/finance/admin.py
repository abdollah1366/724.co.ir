"""پنل مالی: صورتحساب، دریافت، استرداد، قرارداد، تسویه و حقوق.

هر عملیات حساس (ثبت نهایی، تأیید، محاسبه، تغییر وضعیت قرارداد) دکمه جداگانه دارد که مجوز سمت سرور را بررسی می‌کند.
اگر قابلیت مربوط (feature flag) خاموش باشد، بخش از فهرست پنل پنهان می‌شود ولی داده‌ها حذف نمی‌شوند.
"""

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse
from django.utils.html import format_html

from apps.audit.admin_utils import DocumentAdmin, LockedInlineMixin
from apps.core import flags

from .models import (
    Contract, ContractRule, ContractVersion, EmployeeLoan, Invoice, InvoiceLine, PayItemType, PaymentAllocation, PayrollItem,
    PayrollLine, PayrollProfile, PayrollRun, ProfilePayItem, ProviderSettlement, Receipt, ReceiptTender, Refund, SettlementLine,
    TaxBracket,
)
from .services import billing, contracts, payroll, settlements


class FlaggedMixin:
    feature = "finance"

    def has_module_permission(self, request):
        return flags.is_enabled(self.feature) and super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return flags.is_enabled(self.feature) and super().has_view_permission(request, obj)


class ActionButtonsMixin:
    """دکمه‌های عملیات روی صفحه یک رکورد: buttons = [(نام، عنوان، مجوز، نام متد سرویس، شرط نمایش)]."""

    change_form_template = "admin/finance/change_form.html"
    buttons = ()

    def get_urls(self):
        info = self.model._meta.app_label, self.model._meta.model_name
        return [path("<int:pk>/do/<str:action>/", self.admin_site.admin_view(self.button_view), name="%s_%s_do" % info)] + \
            super().get_urls()

    def _buttons_for(self, request, obj):
        info = self.model._meta.app_label, self.model._meta.model_name
        result = []
        for name, label, perm, _, visible in self.buttons:
            if request.user.has_perm(perm) and visible(obj):
                result.append({"name": name, "label": label, "url": reverse("admin:%s_%s_do" % info, args=[obj.pk, name])})
        return result

    def render_change_form(self, request, context, add=False, change=False, form_url="", obj=None):
        if obj is not None:
            context["action_buttons"] = self._buttons_for(request, obj)
        return super().render_change_form(request, context, add, change, form_url, obj)

    def button_view(self, request, pk, action):
        if request.method != "POST":
            raise PermissionDenied
        spec = next((b for b in self.buttons if b[0] == action), None)
        obj = get_object_or_404(self.model, pk=pk)
        if spec is None or not request.user.has_perm(spec[2]) or not spec[4](obj):
            raise PermissionDenied
        try:
            getattr(self, spec[3])(request, obj)
        except (ValidationError, PermissionError) as exc:
            text = " ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
            self.message_user(request, text, messages.ERROR)
        else:
            self.message_user(request, f"{spec[1]}: انجام شد.", messages.SUCCESS)
        info = self.model._meta.app_label, self.model._meta.model_name
        return redirect(reverse("admin:%s_%s_change" % info, args=[obj.pk]))


# ---------------------------------------------------------------- صورتحساب، دریافت، استرداد
class InvoiceLineInline(LockedInlineMixin, admin.TabularInline):
    model = InvoiceLine
    fields = ("service_code", "description", "quantity", "unit_price", "discount", "insurer_share", "provider_id", "provider_name")


@admin.register(Invoice)
class InvoiceAdmin(FlaggedMixin, DocumentAdmin):
    feature = "finance.billing"
    list_display = ("__str__", "date", "module", "patient_name", "provider_name", "gross", "patient_share", "paid_amount",
                    "payment_state", "status")
    list_filter = ("status", "module", "cost_center")
    search_fields = ("number", "patient_name", "source_ref", "provider_name")
    inlines = [InvoiceLineInline]
    fields = ("module", "patient_id", "patient_user_id", "patient_name", "provider_id", "provider_name", "cost_center",
              "revenue_account", "insurer", "insurance_ref", "discount_reason", "date", "note", "source_type", "source_id",
              "source_ref", "gross", "discount", "insurer_share", "patient_share", "paid_amount")
    readonly_fields = ("gross", "discount", "insurer_share", "patient_share", "paid_amount", "source_type", "source_id")

    @admin.display(description="وضعیت پرداخت")
    def payment_state(self, obj):
        return obj.payment_status

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        if form.instance.status == Invoice.Status.DRAFT:
            billing.recalculate(form.instance)


class TenderInline(LockedInlineMixin, admin.TabularInline):
    model = ReceiptTender
    fields = ("method", "money_account", "amount", "reference_no")


class AllocationInline(LockedInlineMixin, admin.TabularInline):
    model = PaymentAllocation
    fields = ("invoice", "amount")
    raw_id_fields = ("invoice",)


@admin.register(Receipt)
class ReceiptAdmin(FlaggedMixin, DocumentAdmin):
    feature = "finance.billing"
    list_display = ("__str__", "date", "patient_name", "total_amount", "status")
    search_fields = ("number", "patient_name", "source_ref")
    inlines = [TenderInline, AllocationInline]
    fields = ("patient_id", "patient_user_id", "patient_name", "date", "source_ref", "note")

    @admin.display(description="مبلغ (ریال)")
    def total_amount(self, obj):
        return f"{obj.total:,}"


@admin.register(Refund)
class RefundAdmin(ActionButtonsMixin, FlaggedMixin, DocumentAdmin):
    feature = "finance.billing"
    list_display = ("__str__", "date", "patient_name", "amount", "invoice", "approved_by", "status")
    search_fields = ("number", "patient_name", "reference_no")
    raw_id_fields = ("invoice",)
    readonly_fields = ("approved_by", "approved_at")
    buttons = [("approve", "تأیید استرداد", "finance.approve_refund", "do_approve",
                lambda o: o.status == Refund.Status.DRAFT and not o.approved_at)]

    def do_approve(self, request, obj):
        billing.approve_refund(obj, request.user)


# ---------------------------------------------------------------- قراردادها
class ContractVersionInline(admin.TabularInline):
    model = ContractVersion
    extra = 0
    fields = ("version", "effective_from", "status", "fixed_monthly", "approved_by", "approved_at")
    readonly_fields = fields
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Contract)
class ContractAdmin(ActionButtonsMixin, FlaggedMixin, admin.ModelAdmin):
    feature = "finance.contracts"
    list_display = ("number", "title", "kind", "party", "provider_name", "start_date", "end_date", "status")
    list_filter = ("kind", "status")
    search_fields = ("number", "title", "party__name", "provider_name")
    readonly_fields = ("number", "status", "status_reason", "terminated_on", "created_by")
    inlines = [ContractVersionInline]
    buttons = [
        ("activate", "فعال‌سازی", "finance.manage_contract_status", "do_activate",
         lambda o: o.status in (Contract.Status.DRAFT, Contract.Status.SUSPENDED)),
        ("suspend", "تعلیق", "finance.manage_contract_status", "do_suspend", lambda o: o.status == Contract.Status.ACTIVE),
        ("terminate", "خاتمه از امروز", "finance.manage_contract_status", "do_terminate",
         lambda o: o.status in (Contract.Status.ACTIVE, Contract.Status.SUSPENDED)),
        ("new_version", "نسخه جدید (کپی آخرین نسخه)", "finance.add_contractversion", "do_new_version",
         lambda o: o.status not in (Contract.Status.TERMINATED, Contract.Status.EXPIRED)),
    ]

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def do_activate(self, request, obj):
        contracts.set_status(obj, Contract.Status.ACTIVE, request.user, "فعال‌سازی از پنل")

    def do_suspend(self, request, obj):
        contracts.set_status(obj, Contract.Status.SUSPENDED, request.user, "تعلیق از پنل")

    def do_terminate(self, request, obj):
        contracts.set_status(obj, Contract.Status.TERMINATED, request.user, "خاتمه از پنل")

    def do_new_version(self, request, obj):
        contracts.new_version(obj, request.user)


class ContractRuleInline(admin.TabularInline):
    model = ContractRule
    extra = 1
    fields = ("module", "service_code", "basis", "percent", "amount")

    def _editable(self, obj):
        return obj is None or obj.status == ContractVersion.Status.DRAFT

    def has_add_permission(self, request, obj=None):
        return self._editable(obj) and super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return self._editable(obj) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self._editable(obj) and super().has_delete_permission(request, obj)


@admin.register(ContractVersion)
class ContractVersionAdmin(ActionButtonsMixin, FlaggedMixin, admin.ModelAdmin):
    feature = "finance.contracts"
    list_display = ("__str__", "status", "fixed_monthly", "approved_by")
    list_filter = ("status",)
    inlines = [ContractRuleInline]
    readonly_fields = ("version", "status", "created_by", "approved_by", "approved_at", "review_note")
    buttons = [
        ("submit", "ارسال برای تأیید", "finance.change_contractversion", "do_submit",
         lambda o: o.status in (ContractVersion.Status.DRAFT, ContractVersion.Status.REJECTED)),
        ("approve", "تأیید نسخه", "finance.approve_contractversion", "do_approve",
         lambda o: o.status == ContractVersion.Status.SUBMITTED),
        ("reject", "رد و برگشت به پیش‌نویس", "finance.approve_contractversion", "do_reject",
         lambda o: o.status == ContractVersion.Status.SUBMITTED),
    ]

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.is_locked:
            return [f.name for f in self.model._meta.fields]
        return super().get_readonly_fields(request, obj)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def do_submit(self, request, obj):
        contracts.submit_version(obj, request.user)

    def do_approve(self, request, obj):
        contracts.approve_version(obj, request.user)

    def do_reject(self, request, obj):
        contracts.reject_version(obj, request.user, "رد از پنل")


class SettlementLineInline(admin.TabularInline):
    model = SettlementLine
    extra = 0
    can_delete = False
    fields = ("kind", "description", "base_amount", "amount", "detail")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ProviderSettlement)
class ProviderSettlementAdmin(ActionButtonsMixin, FlaggedMixin, DocumentAdmin):
    feature = "finance.settlements"
    list_display = ("__str__", "contract", "year", "month", "total", "finance_approved_by", "manager_approved_by", "status")
    list_filter = ("status", "year", "month")
    inlines = [SettlementLineInline]
    fields = ("contract", "year", "month", "note", "period_start", "period_end", "total", "calculated_at", "calc_hash",
              "finance_approved_by", "finance_approved_at", "manager_approved_by", "manager_approved_at")
    readonly_fields = ("period_start", "period_end", "total", "calculated_at", "calc_hash", "finance_approved_by",
                       "finance_approved_at", "manager_approved_by", "manager_approved_at")
    buttons = [
        ("calculate", "محاسبه / محاسبه دوباره", "finance.calculate_settlement", "do_calculate",
         lambda o: o.status == ProviderSettlement.Status.DRAFT),
        ("approve_finance", "تأیید مالی", "finance.approve_settlement_finance", "do_approve_finance",
         lambda o: o.status == ProviderSettlement.Status.DRAFT and o.calculated_at and not o.finance_approved_at),
        ("approve_manager", "تأیید مدیریت", "finance.approve_settlement_manager", "do_approve_manager",
         lambda o: o.status == ProviderSettlement.Status.DRAFT and o.finance_approved_at and not o.manager_approved_at),
    ]

    def save_model(self, request, obj, form, change):
        if not change:
            obj.period_start, obj.period_end = settlements.month_bounds(obj.year, obj.month)
            obj.date = obj.period_end
        super().save_model(request, obj, form, change)
        if not change:
            settlements.calculate(obj, request.user)

    def do_calculate(self, request, obj):
        settlements.calculate(obj, request.user)

    def do_approve_finance(self, request, obj):
        settlements.approve_finance(obj, request.user)

    def do_approve_manager(self, request, obj):
        settlements.approve_manager(obj, request.user)


# ---------------------------------------------------------------- حقوق و دستمزد
class PayrollFlagged(FlaggedMixin):
    feature = "finance.payroll"


@admin.register(PayItemType)
class PayItemTypeAdmin(PayrollFlagged, admin.ModelAdmin):
    list_display = ("code", "name", "kind", "calc", "rate", "taxable", "insurable", "account", "is_active")
    list_filter = ("kind", "is_active")


@admin.register(TaxBracket)
class TaxBracketAdmin(PayrollFlagged, admin.ModelAdmin):
    list_display = ("year", "from_amount", "to_amount", "rate")
    list_filter = ("year",)


class ProfileItemInline(admin.TabularInline):
    model = ProfilePayItem
    extra = 0


@admin.register(PayrollProfile)
class PayrollProfileAdmin(PayrollFlagged, admin.ModelAdmin):
    list_display = ("personnel_no", "party", "job_title", "cost_center", "base_salary", "is_active")
    list_filter = ("is_active", "cost_center")
    search_fields = ("personnel_no", "party__name")
    raw_id_fields = ("user",)
    inlines = [ProfileItemInline]


@admin.register(EmployeeLoan)
class EmployeeLoanAdmin(PayrollFlagged, DocumentAdmin):
    list_display = ("__str__", "profile", "amount", "installment", "remaining", "status")

    @admin.display(description="مانده")
    def remaining(self, obj):
        return f"{payroll.loan_remaining(obj):,}" if obj.status == EmployeeLoan.Status.POSTED else "—"


class PayrollLineInline(LockedInlineMixin, admin.TabularInline):
    model = PayrollLine
    fields = ("profile", "work_days", "absence_days", "leave_days", "overtime_hours", "bonus", "commission",
              "advance_deduction", "other_deduction", "gross", "deductions", "employer_cost", "net", "payslip")
    readonly_fields = ("profile", "gross", "deductions", "employer_cost", "net", "payslip")

    @admin.display(description="فیش")
    def payslip(self, obj):
        if not obj.pk:
            return ""
        return format_html('<a href="{}">جزئیات</a>', reverse("finance:payslip", args=[obj.pk]))

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(PayrollRun)
class PayrollRunAdmin(ActionButtonsMixin, PayrollFlagged, DocumentAdmin):
    list_display = ("__str__", "year", "month", "calculated_at", "approved_by", "status")
    fields = ("year", "month", "note", "calculated_at", "approved_by", "approved_at")
    readonly_fields = ("calculated_at", "approved_by", "approved_at")
    inlines = [PayrollLineInline]
    action_help = "پس از تغییر کارکرد یا ورودی‌ها، «محاسبه» را بزنید؛ محاسبه دوباره تأیید قبلی را باطل می‌کند."
    buttons = [
        ("calculate", "محاسبه / محاسبه دوباره", "finance.change_payrollrun", "do_calculate",
         lambda o: o.status == PayrollRun.Status.DRAFT),
        ("approve", "تأیید لیست حقوق", "finance.approve_payrollrun", "do_approve",
         lambda o: o.status == PayrollRun.Status.DRAFT and o.calculated_at and not o.approved_at),
    ]

    def render_change_form(self, request, context, *args, **kwargs):
        context["action_help"] = self.action_help
        return super().render_change_form(request, context, *args, **kwargs)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not change:
            payroll.calculate_run(obj, request.user)

    def do_calculate(self, request, obj):
        payroll.calculate_run(obj, request.user)

    def do_approve(self, request, obj):
        payroll.approve_run(obj, request.user)


@admin.register(PayrollItem)
class PayrollItemAdmin(PayrollFlagged, admin.ModelAdmin):
    list_display = ("run", "line", "item_type", "amount", "detail")
    list_filter = ("item_type",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

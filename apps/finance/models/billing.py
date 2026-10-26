"""صورتحساب خدمات درمانی، دریافت (ترکیبی و علی‌الحساب) و استرداد.

مالک «انجام خدمت» ماژول تخصصی است (ویزیت در پرونده سلامت، جلسه در توانبخشی، مشاوره در telemed). صورتحساب فقط با
شناسه مرجع آن خدمت ساخته می‌شود و هر منبع حداکثر یک صورتحساب فعال دارد (unique_invoice_source). اطلاعات بیمار در حد
صدور صورتحساب نگه داشته می‌شود: شناسه پرونده، نام و کد/عنوان خدمت؛ تشخیص و یادداشت بالینی هرگز.
"""

from django.conf import settings
from django.db import models

from apps.accounting.models import Account, CostCenter, Party
from apps.audit.documents import DocumentLine, PostedDocument
from apps.core.fields import unique_when


class TenderMethod(models.TextChoices):
    CASH = "cash", "نقد"
    CARD = "card", "کارتخوان"
    BANK = "bank", "انتقال بانکی"
    CHEQUE = "cheque", "چک"
    ONLINE = "online", "درگاه اینترنتی"
    ADVANCE = "advance", "از علی‌الحساب بیمار"


class PatientRef(models.Model):
    """ارجاع حداقلی به بیمار: شناسه پرونده سلامت و/یا کاربر پرتال و نام برای چاپ. کلید خارجی به پرونده نیست."""

    patient_id = models.PositiveBigIntegerField("شناسه پرونده سلامت", null=True, blank=True, db_index=True)
    patient_user_id = models.PositiveBigIntegerField("شناسه کاربر بیمار", null=True, blank=True, db_index=True)
    patient_name = models.CharField("نام بیمار / پرداخت‌کننده", max_length=150)

    class Meta:
        abstract = True

    @property
    def patient_key(self):
        return ("p", self.patient_id) if self.patient_id else ("u", self.patient_user_id)


class Invoice(PatientRef, PostedDocument):
    NUMBER_PREFIX = "INV"

    class Module(models.TextChoices):
        CLINIC = "clinic", "درمانگاه / ویزیت"
        REHAB = "rehab", "توانبخشی"
        TELEMED = "telemed", "مشاوره آنلاین"
        OTHER = "other", "سایر خدمات"

    module = models.CharField("بخش ارائه‌دهنده", max_length=10, choices=Module.choices, default=Module.CLINIC)
    source_type = models.CharField("نوع منبع", max_length=60, blank=True, help_text="مثل ehr.encounter")
    source_id = models.CharField("شناسه منبع", max_length=64, blank=True)
    source_ref = models.CharField("مرجع خدمت", max_length=60, blank=True, help_text="کد مشاوره، شماره نوبت و ...")
    event = models.ForeignKey("core.OutboxEvent", on_delete=models.PROTECT, null=True, blank=True, related_name="+",
                              verbose_name="رویداد منبع", editable=False)
    provider_id = models.PositiveBigIntegerField("شناسه پزشک / ارائه‌دهنده", null=True, blank=True, db_index=True)
    provider_name = models.CharField("پزشک / ارائه‌دهنده", max_length=150, blank=True)
    cost_center = models.ForeignKey(CostCenter, on_delete=models.PROTECT, related_name="+", verbose_name="مرکز درآمد")
    revenue_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="+", verbose_name="حساب درآمد",
        limit_choices_to={"type": Account.Type.REVENUE},
    )
    insurer = models.ForeignKey(
        Party, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name="بیمه / سازمان طرف قرارداد",
        limit_choices_to={"kind__in": [Party.Kind.INSURER, Party.Kind.CUSTOMER]},
    )
    insurance_ref = models.CharField("شماره بیمه / کد تأیید", max_length=60, blank=True)
    discount_reason = models.CharField("علت تخفیف", max_length=200, blank=True)
    gross = models.PositiveBigIntegerField("مبلغ پایه (ریال)", default=0, editable=False)
    discount = models.PositiveBigIntegerField("تخفیف (ریال)", default=0, editable=False)
    insurer_share = models.PositiveBigIntegerField("سهم بیمه / سازمان (ریال)", default=0, editable=False)
    patient_share = models.PositiveBigIntegerField("سهم بیمار (ریال)", default=0, editable=False)
    paid_amount = models.PositiveBigIntegerField("وصول‌شده از بیمار (ریال)", default=0, editable=False)

    source_active = unique_when(~models.Q(source_id="") & ~models.Q(status="reversed"))

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "صورتحساب خدمت"
        verbose_name_plural = "صورتحساب‌های خدمات درمانی"
        permissions = [
            ("post_invoice", "ثبت نهایی صورتحساب"), ("reverse_invoice", "ابطال صورتحساب"),
            ("give_invoice_discount", "دادن تخفیف در صورتحساب"), ("use_cashier", "صندوق درمانگاه (دریافت از بیمار)"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["source_type", "source_id", "source_active"], name="unique_invoice_source"),
            models.CheckConstraint(condition=models.Q(discount__lte=models.F("gross")), name="invoice_discount_lte_gross"),
            models.CheckConstraint(condition=models.Q(paid_amount__lte=models.F("patient_share")) | models.Q(status="reversed"),
                                   name="invoice_paid_lte_patient_share"),
        ]
        indexes = [models.Index(fields=["patient_id", "status"]), models.Index(fields=["provider_id", "date"])]

    @property
    def balance_due(self):
        """مانده قابل دریافت از بیمار؛ برای صورتحساب ابطال‌شده، منفی یعنی بستانکاری بیمار (قابل استرداد)."""
        if self.status == self.Status.REVERSED:
            return -self.paid_amount
        if self.status == self.Status.DRAFT:
            return 0
        return self.patient_share - self.paid_amount

    @property
    def payment_status(self):
        if self.status == self.Status.DRAFT:
            return "پیش‌نویس"
        if self.status == self.Status.REVERSED:
            return "ابطال؛ قابل استرداد" if self.paid_amount else "ابطال‌شده"
        if self.patient_share == 0 or self.paid_amount >= self.patient_share:
            return "تسویه"
        return "پرداخت ناقص" if self.paid_amount else "پرداخت‌نشده"

    def validate_for_post(self):
        from ..services import billing

        billing.validate_invoice(self)

    def perform_post(self, user):
        from ..services import billing

        billing.post_invoice(self, user)

    def validate_for_reverse(self):
        from ..services import billing

        billing.validate_invoice_reverse(self)

    def perform_reverse(self, user):
        from ..services import billing

        billing.reverse_invoice(self, user)


class InvoiceLine(DocumentLine):
    DOCUMENT_FIELD = "invoice"

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines", verbose_name="صورتحساب")
    service_code = models.CharField("کد خدمت / تعرفه", max_length=20, blank=True)
    description = models.CharField("شرح خدمت", max_length=200)
    quantity = models.PositiveIntegerField("تعداد", default=1)
    unit_price = models.PositiveBigIntegerField("مبلغ واحد (ریال)")
    discount = models.PositiveBigIntegerField("تخفیف (ریال)", default=0)
    insurer_share = models.PositiveBigIntegerField("سهم بیمه (ریال)", default=0)
    provider_id = models.PositiveBigIntegerField("شناسه پزشک", null=True, blank=True)
    provider_name = models.CharField("پزشک / ارائه‌دهنده", max_length=150, blank=True)
    source_line_ref = models.CharField("مرجع ردیف در ماژول منبع", max_length=64, blank=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "ردیف صورتحساب"
        verbose_name_plural = "ردیف‌های صورتحساب"

    def __str__(self):
        return f"{self.description} × {self.quantity}"

    @property
    def gross(self):
        return self.quantity * self.unit_price

    @property
    def net(self):
        return self.gross - self.discount

    @property
    def patient_share(self):
        return self.net - self.insurer_share


class Receipt(PatientRef, PostedDocument):
    """دریافت از بیمار با یک یا چند روش (پرداخت ترکیبی)، تخصیص به صورتحساب‌ها و مازاد به‌عنوان علی‌الحساب."""

    NUMBER_PREFIX = "RCP"

    idempotency_key = models.CharField("کلید یکتایی درخواست", max_length=64, unique=True, null=True, blank=True,
                                       editable=False)
    source_ref = models.CharField("مرجع", max_length=60, blank=True)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "دریافت از بیمار"
        verbose_name_plural = "دریافت‌ها از بیماران"
        permissions = [("post_receipt", "ثبت نهایی دریافت از بیمار"), ("reverse_receipt", "برگشت دریافت از بیمار")]

    @property
    def total(self):
        return sum(t.amount for t in self.tenders.all())

    @property
    def allocated(self):
        return sum(a.amount for a in self.allocations.all())

    @property
    def advance(self):
        return self.total - self.allocated

    def validate_for_post(self):
        from ..services import billing

        billing.validate_receipt(self)

    def perform_post(self, user):
        from ..services import billing

        billing.post_receipt(self, user)

    def validate_for_reverse(self):
        from ..services import billing

        billing.validate_receipt_reverse(self)

    def perform_reverse(self, user):
        from ..services import billing

        billing.reverse_receipt(self, user)


class ReceiptTender(DocumentLine):
    DOCUMENT_FIELD = "receipt"

    receipt = models.ForeignKey(Receipt, on_delete=models.CASCADE, related_name="tenders", verbose_name="دریافت")
    method = models.CharField("روش", max_length=8, choices=TenderMethod.choices)
    money_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name="صندوق / حساب بانک",
        limit_choices_to={"is_cash": True}, help_text="خالی = حساب پیش‌فرض روش",
    )
    amount = models.PositiveBigIntegerField("مبلغ (ریال)")
    reference_no = models.CharField("شماره پیگیری / چک", max_length=64, blank=True)

    class Meta:
        verbose_name = "روش پرداخت"
        verbose_name_plural = "روش‌های پرداخت"
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name="tender_amount_positive")]


class PaymentAllocation(DocumentLine):
    DOCUMENT_FIELD = "receipt"

    receipt = models.ForeignKey(Receipt, on_delete=models.CASCADE, related_name="allocations", verbose_name="دریافت")
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="allocations", verbose_name="صورتحساب")
    amount = models.PositiveBigIntegerField("مبلغ تخصیص (ریال)")

    class Meta:
        verbose_name = "تخصیص به صورتحساب"
        verbose_name_plural = "تخصیص به صورتحساب‌ها"
        constraints = [
            models.UniqueConstraint(fields=["receipt", "invoice"], name="unique_allocation_per_invoice"),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="allocation_amount_positive"),
        ]


class Refund(PatientRef, PostedDocument):
    """استرداد وجه به بیمار: از مبلغ پرداختی یک صورتحساب یا از علی‌الحساب. مبالغ بالای آستانه تأیید مستقل می‌خواهند."""

    NUMBER_PREFIX = "RFD"

    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, null=True, blank=True, related_name="refunds",
                                verbose_name="صورتحساب", help_text="خالی = استرداد از علی‌الحساب بیمار")
    amount = models.PositiveBigIntegerField("مبلغ (ریال)")
    method = models.CharField("روش", max_length=8, choices=[c for c in TenderMethod.choices if c[0] != "advance"])
    money_account = models.ForeignKey(Account, on_delete=models.PROTECT, null=True, blank=True, related_name="+",
                                      verbose_name="صندوق / حساب بانک", limit_choices_to={"is_cash": True})
    reference_no = models.CharField("شماره پیگیری", max_length=64, blank=True)
    reason = models.CharField("علت استرداد", max_length=300)
    idempotency_key = models.CharField("کلید یکتایی", max_length=64, unique=True, null=True, blank=True, editable=False)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name="+", verbose_name="تأییدکننده", editable=False)
    approved_at = models.DateTimeField("زمان تأیید", null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "استرداد وجه"
        verbose_name_plural = "استرداد وجه به بیماران"
        permissions = [("post_refund", "ثبت نهایی (پرداخت) استرداد"), ("approve_refund", "تأیید استرداد وجه"),
                       ("reverse_refund", "برگشت استرداد")]
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name="refund_amount_positive")]

    def validate_for_post(self):
        from ..services import billing

        billing.validate_refund(self)

    def perform_post(self, user):
        from ..services import billing

        billing.post_refund(self, user)

    def perform_reverse(self, user):
        from ..services import billing

        billing.reverse_refund(self, user)

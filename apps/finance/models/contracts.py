"""قراردادها با نسخه‌بندی، و تسویه دوره‌ای پزشکان و ارائه‌دهندگان.

- قرارداد سربرگ است (طرفین، نوع، بازه، وضعیت). شرایط مالی در نسخه‌ها است و هر نسخه از تاریخ مؤثر خودش اعمال می‌شود.
- نسخه تأییدشده تغییرناپذیر است؛ تغییر شرایط یعنی نسخه جدید با تاریخ مؤثر جدید. نسخه‌های قبلی بازنویسی نمی‌شوند و برای
  خدمات قبل از تاریخ مؤثر جدید همچنان معتبرند.
- «تأیید» در این سامانه ثبت تأیید کاربر مجاز است، نه امضای الکترونیکی حقوقی.
"""

from django.conf import settings
from django.db import models

from apps.accounting.models import Party
from apps.audit.documents import DocumentLine, PostedDocument
from apps.core.fields import unique_when


class Contract(models.Model):
    class Kind(models.TextChoices):
        PROVIDER = "provider", "پزشک / ارائه‌دهنده خدمت با کلینیک"
        STAFF = "staff", "استخدامی کارکنان (داروساز، پرستار، اداری)"
        LAB_VENDOR = "lab_vendor", "آزمایشگاه یا ارائه‌دهنده خدمات طرف قرارداد"
        SUPPLIER = "supplier", "تأمین‌کننده"
        CORPORATE = "corporate", "شرکت‌ها و سازمان‌ها"
        INSURER = "insurer", "بیمه و پرداخت‌کننده"
        SERVICE = "service", "خدمات و تجهیزات"

    class Status(models.TextChoices):
        DRAFT = "draft", "پیش‌نویس"
        ACTIVE = "active", "فعال"
        SUSPENDED = "suspended", "تعلیق‌شده"
        TERMINATED = "terminated", "خاتمه‌یافته"
        EXPIRED = "expired", "منقضی"

    number = models.CharField("شماره قرارداد", max_length=24, unique=True, editable=False)
    kind = models.CharField("نوع قرارداد", max_length=10, choices=Kind.choices)
    title = models.CharField("عنوان", max_length=200)
    party = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="contracts", verbose_name="طرف قرارداد")
    provider_id = models.PositiveBigIntegerField(
        "شناسه پزشک (برای قرارداد پزشک)", null=True, blank=True, db_index=True,
        help_text="شناسه ارائه‌دهنده در درمانگاه؛ سهم از صورتحساب‌هایی که این پزشک ارائه‌دهنده آن است",
    )
    provider_name = models.CharField("نام پزشک", max_length=150, blank=True)
    start_date = models.DateField("تاریخ شروع")
    end_date = models.DateField("تاریخ پایان", null=True, blank=True, help_text="خالی = نامحدود")
    status = models.CharField("وضعیت", max_length=10, choices=Status.choices, default=Status.DRAFT, editable=False)
    auto_renew = models.BooleanField("تمدید خودکار", default=False)
    renewal_months = models.PositiveSmallIntegerField("مدت هر تمدید (ماه)", default=12)
    notice_days = models.PositiveSmallIntegerField("مهلت اعلام عدم تمدید / خاتمه (روز)", default=30)
    status_reason = models.CharField("علت آخرین تغییر وضعیت", max_length=300, blank=True, editable=False)
    terminated_on = models.DateField("تاریخ خاتمه", null=True, blank=True, editable=False)
    notes = models.TextField("توضیحات و تعهدات غیرمالی", blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+", verbose_name="ایجادکننده", editable=False)
    created_at = models.DateTimeField("زمان ایجاد", auto_now_add=True)

    class Meta:
        ordering = ["-start_date", "-id"]
        verbose_name = "قرارداد"
        verbose_name_plural = "قراردادها"
        permissions = [("manage_contract_status", "فعال‌سازی، تعلیق و خاتمه قرارداد")]
        constraints = [models.CheckConstraint(condition=models.Q(end_date__isnull=True) | models.Q(end_date__gte=models.F("start_date")),
                                              name="contract_end_after_start")]

    def __str__(self):
        return f"{self.number} {self.title}"

    def save(self, *args, **kwargs):
        if not self.number:
            from apps.audit.documents import next_number

            self.number = next_number("CTR", self.start_date)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != self.Status.DRAFT or self.versions.filter(status=ContractVersion.Status.APPROVED).exists():
            raise PermissionError("قرارداد فعال یا دارای نسخه تأییدشده حذف نمی‌شود؛ آن را خاتمه دهید.")
        return super().delete(*args, **kwargs)

    def covers(self, date):
        """آیا قرارداد در این تاریخ برقرار بوده؟ (تعلیق و پیش‌نویس = خیر، خاتمه از تاریخ خاتمه به بعد = خیر)"""
        if self.status in (self.Status.DRAFT, self.Status.SUSPENDED):
            return False
        if date < self.start_date or (self.end_date and date > self.end_date):
            return False
        return not (self.terminated_on and date > self.terminated_on)


class ContractVersion(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "پیش‌نویس"
        SUBMITTED = "submitted", "ارسال برای تأیید"
        APPROVED = "approved", "تأییدشده"
        REJECTED = "rejected", "ردشده"

    class ShareBase(models.TextChoices):
        GROSS = "gross", "مبلغ پایه خدمت (پیش از تخفیف)"
        NET = "net", "مبلغ صورتحساب (پس از تخفیف)"

    contract = models.ForeignKey(Contract, on_delete=models.PROTECT, related_name="versions", verbose_name="قرارداد")
    version = models.PositiveSmallIntegerField("نسخه", editable=False)
    effective_from = models.DateField("تاریخ مؤثر")
    status = models.CharField("وضعیت", max_length=10, choices=Status.choices, default=Status.DRAFT, editable=False)
    share_base = models.CharField("مبنای سهم درصدی از صورتحساب", max_length=5, choices=ShareBase.choices,
                                  default=ShareBase.NET, help_text="ضابطه تخفیف: آیا تخفیف از سهم پزشک هم کم شود")
    fixed_monthly = models.PositiveBigIntegerField("مبلغ ثابت ماهانه (ریال)", default=0, help_text="حقوق یا حق‌الزحمه ثابت")
    period_floor = models.PositiveBigIntegerField("کف پرداخت ماهانه (ریال)", null=True, blank=True)
    period_cap = models.PositiveBigIntegerField("سقف پرداخت ماهانه (ریال)", null=True, blank=True)
    payment_terms_days = models.PositiveSmallIntegerField("مهلت پرداخت پس از تأیید تسویه (روز)", default=10)
    terms = models.TextField("شرایط تسویه، کسورات، تمدید و خاتمه", blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+", verbose_name="ایجادکننده", editable=False)
    created_at = models.DateTimeField("زمان ایجاد", auto_now_add=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name="+", verbose_name="تأییدکننده", editable=False)
    approved_at = models.DateTimeField("زمان تأیید", null=True, blank=True, editable=False)
    review_note = models.CharField("توضیح تأیید / رد", max_length=300, blank=True, editable=False)

    class Meta:
        ordering = ["contract", "-version"]
        verbose_name = "نسخه قرارداد"
        verbose_name_plural = "نسخه‌های قرارداد"
        permissions = [("approve_contractversion", "تأیید نسخه قرارداد")]
        constraints = [
            models.UniqueConstraint(fields=["contract", "version"], name="unique_contract_version"),
            models.CheckConstraint(
                condition=models.Q(period_cap__isnull=True) | models.Q(period_floor__isnull=True)
                | models.Q(period_cap__gte=models.F("period_floor")), name="contract_cap_gte_floor",
            ),
        ]

    def __str__(self):
        return f"{self.contract.number} نسخه {self.version} (از {self.effective_from})"

    @property
    def is_locked(self):
        return self.status in (self.Status.SUBMITTED, self.Status.APPROVED)

    def save(self, *args, **kwargs):
        if self.pk and not getattr(self, "_status_change", False):
            stored = ContractVersion.objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if stored in (self.Status.SUBMITTED, self.Status.APPROVED):
                raise PermissionError("نسخه ارسال‌شده یا تأییدشده قرارداد قابل ویرایش نیست؛ نسخه جدید بسازید.")
        if not self.version:
            last = ContractVersion.objects.filter(contract=self.contract).order_by("-version").values_list("version", flat=True).first()
            self.version = (last or 0) + 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status == self.Status.APPROVED:
            raise PermissionError("نسخه تأییدشده حذف نمی‌شود.")
        return super().delete(*args, **kwargs)


class ContractRule(models.Model):
    """قاعده سهم: اولین قاعده منطبق (دقیق‌ترین) برای هر ردیف صورتحساب اعمال می‌شود."""

    class Basis(models.TextChoices):
        PERCENT_BILLED = "pct_billed", "درصد از مبلغ صورتحساب (درآمد)"
        PERCENT_COLLECTED = "pct_collect", "درصد از مبلغ وصول‌شده از بیمار"
        FIXED_PER_SERVICE = "per_service", "مبلغ ثابت به ازای هر خدمت"

    version = models.ForeignKey(ContractVersion, on_delete=models.CASCADE, related_name="rules", verbose_name="نسخه")
    module = models.CharField("بخش", max_length=10, blank=True, help_text="خالی = همه بخش‌ها",
                              choices=[("", "همه بخش‌ها"), ("clinic", "درمانگاه / ویزیت"), ("rehab", "توانبخشی"),
                                       ("telemed", "مشاوره آنلاین"), ("other", "سایر")])
    service_code = models.CharField("کد خدمت", max_length=20, blank=True, help_text="خالی = همه خدمات")
    basis = models.CharField("مبنا", max_length=11, choices=Basis.choices)
    percent = models.DecimalField("درصد", max_digits=5, decimal_places=2, null=True, blank=True)
    amount = models.PositiveBigIntegerField("مبلغ ثابت هر خدمت (ریال)", null=True, blank=True)

    class Meta:
        ordering = ["version", "id"]
        verbose_name = "قاعده سهم"
        verbose_name_plural = "قواعد سهم"
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(basis="per_service", amount__isnull=False)
                           | (~models.Q(basis="per_service") & models.Q(percent__isnull=False, percent__gte=0, percent__lte=100))),
                name="contract_rule_value",
            )
        ]

    def __str__(self):
        target = self.service_code or self.get_module_display() or "همه"
        value = f"{self.amount:,} ریال" if self.basis == self.Basis.FIXED_PER_SERVICE else f"{self.percent}٪"
        return f"{target}: {value} ({self.get_basis_display()})"

    def save(self, *args, **kwargs):
        if self.version_id and ContractVersion.objects.filter(pk=self.version_id).exclude(status=ContractVersion.Status.DRAFT).exists():
            raise PermissionError("قواعد نسخه ارسال‌شده یا تأییدشده قابل تغییر نیست.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if ContractVersion.objects.filter(pk=self.version_id).exclude(status=ContractVersion.Status.DRAFT).exists():
            raise PermissionError("قواعد نسخه ارسال‌شده یا تأییدشده قابل حذف نیست.")
        return super().delete(*args, **kwargs)

    @property
    def specificity(self):
        return (2 if self.service_code else 0) + (1 if self.module else 0)

    def matches(self, module, service_code):
        return (not self.module or self.module == module) and (not self.service_code or self.service_code == service_code)


class ProviderSettlement(PostedDocument):
    """صورت‌حساب تسویه ماهانه پزشک: محاسبه قطعی از صورتحساب‌ها و دریافت‌ها، دو مرحله تأیید، سپس ثبت سند."""

    NUMBER_PREFIX = "STL"

    contract = models.ForeignKey(Contract, on_delete=models.PROTECT, related_name="settlements", verbose_name="قرارداد")
    year = models.PositiveSmallIntegerField("سال (شمسی)")
    month = models.PositiveSmallIntegerField("ماه (شمسی)")
    period_start = models.DateField("از تاریخ", editable=False)
    period_end = models.DateField("تا تاریخ", editable=False)
    total = models.BigIntegerField("مبلغ قابل پرداخت این دوره (ریال)", default=0, editable=False)
    calculated_at = models.DateTimeField("زمان محاسبه", null=True, blank=True, editable=False)
    calc_hash = models.CharField("اثر محاسبه", max_length=64, blank=True, editable=False,
                                 help_text="چکیده ورودی‌ها و نتیجه؛ محاسبه دوباره با همان داده همین مقدار را می‌دهد")
    finance_approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                            related_name="+", verbose_name="تأیید مالی", editable=False)
    finance_approved_at = models.DateTimeField("زمان تأیید مالی", null=True, blank=True, editable=False)
    manager_approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                            related_name="+", verbose_name="تأیید مدیریت", editable=False)
    manager_approved_at = models.DateTimeField("زمان تأیید مدیریت", null=True, blank=True, editable=False)

    period_active = unique_when(~models.Q(status="reversed"))

    class Meta:
        ordering = ["-year", "-month", "-id"]
        verbose_name = "تسویه پزشک"
        verbose_name_plural = "تسویه‌حساب پزشکان و ارائه‌دهندگان"
        permissions = [
            ("calculate_settlement", "محاسبه تسویه"), ("approve_settlement_finance", "تأیید مالی تسویه"),
            ("approve_settlement_manager", "تأیید مدیریتی تسویه"), ("post_providersettlement", "ثبت نهایی تسویه"),
            ("reverse_providersettlement", "برگشت تسویه"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["contract", "year", "month", "period_active"], name="unique_settlement_period"),
            models.CheckConstraint(condition=models.Q(month__gte=1, month__lte=12), name="settlement_month_range"),
        ]

    def __str__(self):
        return self.number or f"تسویه {self.contract.provider_name or self.contract.party} {self.year}/{self.month:02d}"

    def validate_for_post(self):
        from ..services import settlements

        settlements.validate_settlement_post(self)

    def perform_post(self, user):
        from ..services import settlements

        settlements.post_settlement(self, user)

    def perform_reverse(self, user):
        from ..services import settlements

        settlements.reverse_settlement(self, user)


class SettlementLine(DocumentLine):
    class Kind(models.TextChoices):
        BILLED = "billed", "سهم از صورتحساب"
        BILLED_REVERSAL = "billed_rev", "برگشت سهم (ابطال صورتحساب)"
        COLLECTED = "collected", "سهم از وصولی"
        COLLECTED_REVERSAL = "coll_rev", "برگشت سهم وصولی (استرداد/برگشت دریافت)"
        FIXED = "fixed", "مبلغ ثابت ماهانه"
        FLOOR_CAP = "floor_cap", "تعدیل کف / سقف"
        ADJUSTMENT = "adjust", "پاداش / کسورات دستی"

    DOCUMENT_FIELD = "settlement"

    settlement = models.ForeignKey(ProviderSettlement, on_delete=models.CASCADE, related_name="lines", verbose_name="تسویه")
    kind = models.CharField("نوع", max_length=10, choices=Kind.choices)
    invoice_line = models.ForeignKey("finance.InvoiceLine", on_delete=models.PROTECT, null=True, blank=True, related_name="+",
                                     verbose_name="ردیف صورتحساب")
    contract_version = models.ForeignKey(ContractVersion, on_delete=models.PROTECT, null=True, blank=True, related_name="+",
                                         verbose_name="نسخه قرارداد")
    rule = models.ForeignKey(ContractRule, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name="قاعده")
    base_amount = models.BigIntegerField("مبنای محاسبه (ریال)", default=0)
    amount = models.BigIntegerField("مبلغ (ریال)")
    description = models.CharField("شرح", max_length=300)
    detail = models.JSONField("جزئیات محاسبه", default=dict, blank=True)
    dedupe_key = models.CharField("کلید یکتایی", max_length=80)
    is_void = models.BooleanField("باطل (تسویه برگشت‌خورده)", default=False, editable=False)

    line_active = unique_when(models.Q(is_void=False))

    class Meta:
        ordering = ["id"]
        verbose_name = "ردیف تسویه"
        verbose_name_plural = "ردیف‌های تسویه"
        constraints = [models.UniqueConstraint(fields=["dedupe_key", "line_active"], name="unique_settlement_item")]

    def __str__(self):
        return f"{self.get_kind_display()}: {self.amount:,}"

from django.conf import settings
from django.db import models

from apps.audit.documents import DocumentLine, ImmutableRecord, PostedDocument
from apps.core.fields import unique_when
from apps.core.validators import validate_mobile


class Party(models.Model):
    """طرف حساب: تأمین‌کننده (شرکت پخش)، مشتری، بیمه یا سایر."""

    class Kind(models.TextChoices):
        SUPPLIER = "supplier", "تأمین‌کننده"
        CUSTOMER = "customer", "مشتری"
        INSURER = "insurer", "سازمان بیمه‌گر"
        PROVIDER = "provider", "پزشک / ارائه‌دهنده خدمت"
        EMPLOYEE = "employee", "کارمند"
        OTHER = "other", "سایر"

    kind = models.CharField("نوع", max_length=10, choices=Kind.choices)
    name = models.CharField("نام", max_length=200)
    code = models.CharField("کد طرف حساب", max_length=20, unique=True, null=True, blank=True)
    national_id = models.CharField("شناسه ملی / کد ملی", max_length=11, blank=True)
    economic_code = models.CharField("کد اقتصادی", max_length=14, blank=True)
    license_no = models.CharField("شماره مجوز فعالیت", max_length=60, blank=True, help_text="مثلاً مجوز شرکت پخش از سازمان غذا و دارو")
    phone = models.CharField("تلفن", max_length=20, blank=True)
    mobile = models.CharField("موبایل", max_length=11, blank=True, validators=[validate_mobile])
    address = models.CharField("آدرس", max_length=300, blank=True)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="party", verbose_name="حساب کاربری"
    )
    is_active = models.BooleanField("فعال", default=True)

    national_id_set = unique_when(~models.Q(national_id=""))

    class Meta:
        ordering = ["kind", "name"]
        verbose_name = "طرف حساب"
        verbose_name_plural = "طرف‌های حساب (تأمین‌کننده، مشتری، بیمه)"
        constraints = [
            models.UniqueConstraint(fields=["kind", "national_id", "national_id_set"], name="unique_party_national_id")
        ]

    def __str__(self):
        return self.name


class Account(models.Model):
    class Type(models.TextChoices):
        ASSET = "asset", "دارایی"
        LIABILITY = "liability", "بدهی"
        EQUITY = "equity", "حقوق صاحبان سرمایه"
        REVENUE = "revenue", "درآمد"
        EXPENSE = "expense", "هزینه"

    DEBIT_NORMAL = {Type.ASSET, Type.EXPENSE}

    code = models.CharField("کد حساب", max_length=10, unique=True)
    name = models.CharField("نام حساب", max_length=150)
    type = models.CharField("ماهیت", max_length=10, choices=Type.choices)
    is_cash = models.BooleanField("حساب نقد و بانک", default=False)
    bank_name = models.CharField("نام بانک / صندوق", max_length=80, blank=True)
    account_no = models.CharField("شماره حساب / شبا", max_length=34, blank=True, help_text="برای حساب‌های بانکی")
    is_system = models.BooleanField("حساب سیستمی", default=False, editable=False)
    is_active = models.BooleanField("فعال", default=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "حساب"
        verbose_name_plural = "سرفصل حساب‌ها"

    def __str__(self):
        return f"{self.code} {self.name}"


class CostCenter(models.Model):
    """مرکز هزینه / درآمد (بخش‌های مجموعه). آرتیکل‌ها برای گزارش سود و زیان به تفکیک بخش به آن برچسب می‌خورند."""

    code = models.CharField("کد", max_length=12, unique=True)
    name = models.CharField("نام", max_length=120)
    is_active = models.BooleanField("فعال", default=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "مرکز هزینه / درآمد"
        verbose_name_plural = "مراکز هزینه و درآمد"

    def __str__(self):
        return f"{self.code} {self.name}"


class AccountingPeriod(models.Model):
    """دوره مالی. ثبت سند با تاریخ داخل دوره بسته ممکن نیست؛ اصلاح فقط با سند در دوره باز."""

    class Status(models.TextChoices):
        OPEN = "open", "باز"
        CLOSED = "closed", "بسته"

    name = models.CharField("نام دوره", max_length=60, unique=True, help_text="مثلاً «سال مالی ۱۴۰۵» یا «مهر ۱۴۰۵»")
    start_date = models.DateField("از تاریخ")
    end_date = models.DateField("تا تاریخ")
    is_year_end = models.BooleanField("پایان سال مالی", default=False, help_text="با بستن، سند بستن حساب‌های موقت صادر می‌شود")
    status = models.CharField("وضعیت", max_length=6, choices=Status.choices, default=Status.OPEN, editable=False)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name="بستن توسط",
        editable=False,
    )
    closed_at = models.DateTimeField("زمان بستن", null=True, blank=True, editable=False)
    note = models.CharField("توضیح آخرین تغییر وضعیت", max_length=300, blank=True, editable=False)

    class Meta:
        ordering = ["-start_date"]
        verbose_name = "دوره مالی"
        verbose_name_plural = "دوره‌های مالی"
        permissions = [("close_accountingperiod", "بستن و بازگشایی دوره مالی")]
        constraints = [models.CheckConstraint(condition=models.Q(end_date__gte=models.F("start_date")), name="period_end_after_start")]

    def __str__(self):
        return self.name

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.start_date and self.end_date:
            overlap = AccountingPeriod.objects.filter(start_date__lte=self.end_date, end_date__gte=self.start_date)
            if overlap.exclude(pk=self.pk).exists():
                raise ValidationError("این بازه با دوره دیگری هم‌پوشانی دارد.")
        if self.pk and self.status == self.Status.CLOSED:
            raise ValidationError("دوره بسته قابل ویرایش نیست.")


class JournalEntry(ImmutableRecord):
    """سند حسابداری؛ خودکار از اسناد عملیاتی ساخته می‌شود و پس از ایجاد تغییر نمی‌کند."""

    number = models.CharField("شماره سند حسابداری", max_length=24, unique=True)
    date = models.DateField("تاریخ")
    description = models.CharField("شرح", max_length=300)
    source_type = models.CharField("نوع سند مرجع", max_length=60)
    source_id = models.PositiveBigIntegerField("شناسه سند مرجع")
    source_number = models.CharField("شماره سند مرجع", max_length=24, blank=True)
    is_reversal = models.BooleanField("سند برگشتی", default=False)
    reverses = models.OneToOneField(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="reversed_by_entry", verbose_name="برگشت سند"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name="کاربر"
    )
    created_at = models.DateTimeField("زمان ثبت", auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "سند حسابداری"
        verbose_name_plural = "اسناد حسابداری"
        permissions = [("view_financial_reports", "مشاهده گزارش‌های مالی")]
        constraints = [
            # هر سند عملیاتی فقط یک سند حسابداری اصلی و یک سند برگشتی دارد (جلوگیری از ثبت تکراری).
            models.UniqueConstraint(fields=["source_type", "source_id", "is_reversal"], name="unique_journal_per_source")
        ]

    def __str__(self):
        return f"{self.number} - {self.description}"


class JournalLine(ImmutableRecord):
    entry = models.ForeignKey(JournalEntry, on_delete=models.PROTECT, related_name="lines", verbose_name="سند")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="lines", verbose_name="حساب")
    party = models.ForeignKey(Party, on_delete=models.PROTECT, null=True, blank=True, related_name="lines", verbose_name="طرف حساب")
    cost_center = models.ForeignKey(
        CostCenter, on_delete=models.PROTECT, null=True, blank=True, related_name="lines", verbose_name="مرکز هزینه / درآمد"
    )
    debit = models.PositiveBigIntegerField("بدهکار", default=0)
    credit = models.PositiveBigIntegerField("بستانکار", default=0)
    description = models.CharField("شرح", max_length=200, blank=True)

    class Meta:
        verbose_name = "آرتیکل"
        verbose_name_plural = "آرتیکل‌های حسابداری"
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(debit__gt=0, credit=0) | models.Q(debit=0, credit__gt=0)), name="journal_line_one_side"
            )
        ]

    def __str__(self):
        return f"{self.account}: {self.debit} / {self.credit}"


class PaymentMethod(models.TextChoices):
    CASH = "cash", "نقد"
    CARD = "card", "کارتخوان"
    BANK = "bank", "انتقال بانکی"
    CHEQUE = "cheque", "چک"


class Payment(PostedDocument):
    """دریافت از مشتری/بیمه یا پرداخت به تأمین‌کننده."""

    NUMBER_PREFIX = "PAY"

    class Direction(models.TextChoices):
        RECEIVE = "receive", "دریافت"
        PAY = "pay", "پرداخت"

    direction = models.CharField("نوع", max_length=8, choices=Direction.choices)
    party = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="payments", verbose_name="طرف حساب")
    method = models.CharField("روش", max_length=8, choices=PaymentMethod.choices, default=PaymentMethod.BANK)
    amount = models.PositiveBigIntegerField("مبلغ (ریال)")
    reference_no = models.CharField("شماره پیگیری / چک", max_length=40, blank=True)

    reference_active = unique_when(~models.Q(reference_no="") & ~models.Q(status="reversed"))

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "دریافت / پرداخت"
        verbose_name_plural = "دریافت‌ها و پرداخت‌ها"
        permissions = [("post_payment", "ثبت نهایی دریافت و پرداخت"), ("reverse_payment", "برگشت دریافت و پرداخت")]
        constraints = [
            models.UniqueConstraint(
                fields=["direction", "method", "reference_no", "reference_active"], name="unique_payment_reference"
            )
        ]

    def validate_for_post(self):
        from . import services

        services.validate_payment(self)

    def perform_post(self, user):
        from . import services

        services.post_payment(self, user)

    def perform_reverse(self, user):
        from . import services

        services.reverse_entry_for(self, user)


class Expense(PostedDocument):
    NUMBER_PREFIX = "EXP"

    account = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="+", verbose_name="حساب هزینه",
        limit_choices_to={"type": Account.Type.EXPENSE, "is_system": False},
    )
    amount = models.PositiveBigIntegerField("مبلغ (ریال)")
    method = models.CharField(
        "روش پرداخت", max_length=8, choices=PaymentMethod.choices, blank=True,
        help_text="خالی = نسیه (بدهی به طرف حساب)",
    )
    party = models.ForeignKey(Party, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name="طرف حساب")
    description = models.CharField("شرح هزینه", max_length=300)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "هزینه"
        verbose_name_plural = "هزینه‌ها"
        permissions = [("post_expense", "ثبت نهایی هزینه"), ("reverse_expense", "برگشت هزینه")]

    def validate_for_post(self):
        from . import services

        services.validate_expense(self)

    def perform_post(self, user):
        from . import services

        services.post_expense(self, user)

    def perform_reverse(self, user):
        from . import services

        services.reverse_entry_for(self, user)


class ManualJournal(PostedDocument):
    """سند دستی (اصلاحی، افتتاحیه، بستن یا سایر). پس از ثبت نهایی فقط با برگشت اصلاح می‌شود."""

    NUMBER_PREFIX = "MJV"

    class Kind(models.TextChoices):
        GENERAL = "general", "عمومی / اصلاحی"
        OPENING = "opening", "افتتاحیه"
        CLOSING = "closing", "بستن حساب‌ها"

    kind = models.CharField("نوع", max_length=8, choices=Kind.choices, default=Kind.GENERAL)
    description = models.CharField("شرح سند", max_length=300)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "سند دستی"
        verbose_name_plural = "اسناد دستی (اصلاحی و افتتاحیه)"
        permissions = [("post_manualjournal", "ثبت نهایی سند دستی"), ("reverse_manualjournal", "برگشت سند دستی")]

    def validate_for_post(self):
        from . import services

        services.validate_manual_journal(self)

    def perform_post(self, user):
        from . import services

        services.post_manual_journal(self, user)

    def perform_reverse(self, user):
        from . import services

        services.reverse_entry_for(self, user)


class ManualJournalLine(DocumentLine):
    document = models.ForeignKey(ManualJournal, on_delete=models.CASCADE, related_name="lines", verbose_name="سند")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+", verbose_name="حساب")
    party = models.ForeignKey(Party, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name="طرف حساب")
    cost_center = models.ForeignKey(CostCenter, on_delete=models.PROTECT, null=True, blank=True, related_name="+",
                                    verbose_name="مرکز هزینه / درآمد")
    debit = models.PositiveBigIntegerField("بدهکار", default=0)
    credit = models.PositiveBigIntegerField("بستانکار", default=0)
    description = models.CharField("شرح", max_length=200, blank=True)

    class Meta:
        verbose_name = "ردیف سند دستی"
        verbose_name_plural = "ردیف‌های سند دستی"


class BankReconciliation(models.Model):
    """مغایرت‌گیری حساب بانک/صندوق با صورت‌حساب بانک: آرتیکل‌های تطبیق‌شده علامت می‌خورند و اختلاف نمایش داده می‌شود."""

    class Status(models.TextChoices):
        DRAFT = "draft", "در حال تطبیق"
        FINAL = "final", "نهایی"

    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+", verbose_name="حساب بانک / صندوق",
                                limit_choices_to={"is_cash": True})
    statement_date = models.DateField("تاریخ صورت‌حساب بانک")
    statement_balance = models.BigIntegerField("مانده طبق صورت‌حساب بانک (ریال)")
    status = models.CharField("وضعیت", max_length=5, choices=Status.choices, default=Status.DRAFT, editable=False)
    note = models.CharField("توضیح اختلاف", max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+", verbose_name="ایجادکننده", editable=False)
    created_at = models.DateTimeField("زمان ایجاد", auto_now_add=True)
    finalized_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                     related_name="+", verbose_name="نهایی‌کننده", editable=False)
    finalized_at = models.DateTimeField("زمان نهایی شدن", null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-statement_date", "-id"]
        verbose_name = "مغایرت‌گیری بانک"
        verbose_name_plural = "مغایرت‌گیری بانک و صندوق"

    def __str__(self):
        return f"{self.account} تا {self.statement_date}"


class ReconciledLine(models.Model):
    """آرتیکل بانکی که با ردیف صورت‌حساب بانک تطبیق داده شده. هر آرتیکل فقط یک بار تطبیق می‌خورد."""

    reconciliation = models.ForeignKey(BankReconciliation, on_delete=models.CASCADE, related_name="items", verbose_name="مغایرت‌گیری")
    line = models.OneToOneField(JournalLine, on_delete=models.PROTECT, related_name="reconciled", verbose_name="آرتیکل")
    bank_reference = models.CharField("شماره پیگیری در صورت‌حساب بانک", max_length=60, blank=True)

    class Meta:
        verbose_name = "آرتیکل تطبیق‌شده"
        verbose_name_plural = "آرتیکل‌های تطبیق‌شده"

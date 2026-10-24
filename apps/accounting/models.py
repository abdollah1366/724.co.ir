from django.conf import settings
from django.db import models

from apps.audit.documents import ImmutableRecord, PostedDocument
from apps.core.fields import unique_when
from apps.core.validators import validate_mobile


class Party(models.Model):
    """طرف حساب: تأمین‌کننده (شرکت پخش)، مشتری، بیمه یا سایر."""

    class Kind(models.TextChoices):
        SUPPLIER = "supplier", "تأمین‌کننده"
        CUSTOMER = "customer", "مشتری"
        INSURER = "insurer", "سازمان بیمه‌گر"
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
    is_system = models.BooleanField("حساب سیستمی", default=False, editable=False)
    is_active = models.BooleanField("فعال", default=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "حساب"
        verbose_name_plural = "سرفصل حساب‌ها"

    def __str__(self):
        return f"{self.code} {self.name}"


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

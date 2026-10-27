"""حقوق و دستمزد.

هیچ نرخ قانونی (بیمه، مالیات، ضریب اضافه‌کاری) در کد نیست: همه در «اقلام حقوق» و «جدول مالیات» پنل تعریف می‌شوند.
اقلام قانونی به‌صورت غیرفعال و بدون نرخ ساخته می‌شوند تا مسئول منابع انسانی پس از وارد کردن نرخ معتبر آن‌ها را فعال کند.
ماژول حضور و غیاب وجود ندارد؛ روزهای کارکرد، غیبت و اضافه‌کاری در ردیف حقوق هر ماه وارد می‌شود.
"""

from django.conf import settings
from django.db import models

from apps.accounting.models import Account, CostCenter, Party
from apps.audit.documents import DocumentLine, PostedDocument
from apps.core.fields import unique_when


class PayItemType(models.Model):
    class Kind(models.TextChoices):
        EARNING = "earning", "مزایا / کارکرد"
        DEDUCTION = "deduction", "کسور"
        EMPLOYER = "employer", "سهم کارفرما (هزینه، کسر نمی‌شود)"

    class Calc(models.TextChoices):
        BASE = "base", "حقوق پایه (به نسبت روزهای کارکرد)"
        PROFILE = "profile", "مبلغ ثابت از پرونده کارمند (به نسبت روزهای کارکرد)"
        OVERTIME = "overtime", "اضافه‌کاری: ساعت × نرخ ساعتی × ضریب"
        MANUAL = "manual", "ورود دستی در ردیف ماه"
        PCT_INSURABLE = "pct_ins", "درصد از جمع مشمول بیمه"
        PCT_GROSS = "pct_gross", "درصد از حقوق ناخالص"
        TAX = "tax", "مالیات طبق جدول پلکانی"
        LOAN = "loan", "قسط وام"

    code = models.CharField("کد", max_length=20, unique=True)
    name = models.CharField("عنوان", max_length=80)
    kind = models.CharField("نوع", max_length=9, choices=Kind.choices)
    calc = models.CharField("روش محاسبه", max_length=9, choices=Calc.choices)
    rate = models.DecimalField("نرخ / ضریب", max_digits=7, decimal_places=4, null=True, blank=True,
                               help_text="درصد برای اقلام درصدی، ضریب برای اضافه‌کاری؛ مقدار را از مقررات معتبر وارد کنید")
    taxable = models.BooleanField("مشمول مالیات", default=False)
    insurable = models.BooleanField("مشمول بیمه", default=False)
    reduces_taxable = models.BooleanField("از درآمد مشمول مالیات کسر شود", default=False,
                                          help_text="برای کسوری که طبق مقررات معاف از مالیات است")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+", verbose_name="حساب (هزینه یا بدهی)")
    liability_account = models.ForeignKey(Account, on_delete=models.PROTECT, null=True, blank=True, related_name="+",
                                          verbose_name="حساب بدهی سهم کارفرما")
    order = models.PositiveSmallIntegerField("ترتیب", default=0)
    is_active = models.BooleanField("فعال", default=True)

    class Meta:
        ordering = ["order", "code"]
        verbose_name = "قلم حقوق"
        verbose_name_plural = "اقلام حقوق و کسور (قابل تنظیم)"

    def __str__(self):
        return self.name


class TaxBracket(models.Model):
    """پله‌های مالیات ماهانه حقوق؛ از روی جدول ابلاغی هر سال وارد شود."""

    year = models.PositiveSmallIntegerField("سال (شمسی)")
    from_amount = models.PositiveBigIntegerField("از مبلغ ماهانه (ریال)")
    to_amount = models.PositiveBigIntegerField("تا مبلغ ماهانه (ریال)", null=True, blank=True, help_text="خالی = بیشتر")
    rate = models.DecimalField("نرخ (درصد)", max_digits=5, decimal_places=2)

    class Meta:
        ordering = ["year", "from_amount"]
        verbose_name = "پله مالیات حقوق"
        verbose_name_plural = "جدول مالیات حقوق"
        constraints = [models.UniqueConstraint(fields=["year", "from_amount"], name="unique_tax_bracket")]

    def __str__(self):
        return f"{self.year}: از {self.from_amount:,} — {self.rate}٪"


class PayrollProfile(models.Model):
    """پرونده مالی کارمند. اطلاعات بانکی و حقوق فقط برای منابع انسانی، مدیر مالی و خود کارمند قابل دیدن است."""

    party = models.OneToOneField(Party, on_delete=models.PROTECT, related_name="payroll_profile", verbose_name="طرف حساب کارمند",
                                 limit_choices_to={"kind": Party.Kind.EMPLOYEE})
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                related_name="payroll_profile", verbose_name="حساب کاربری (برای دیدن فیش)")
    personnel_no = models.CharField("شماره پرسنلی", max_length=20, unique=True)
    job_title = models.CharField("سمت", max_length=100)
    cost_center = models.ForeignKey(CostCenter, on_delete=models.PROTECT, related_name="+", verbose_name="مرکز هزینه")
    contract = models.ForeignKey("finance.Contract", on_delete=models.PROTECT, null=True, blank=True, related_name="+",
                                 verbose_name="قرارداد استخدامی", limit_choices_to={"kind": "staff"})
    base_salary = models.PositiveBigIntegerField("حقوق پایه ماهانه (ریال)")
    monthly_hours = models.PositiveSmallIntegerField("ساعت کار موظف ماهانه", default=0,
                                                     help_text="برای نرخ ساعتی اضافه‌کاری؛ طبق قرارداد وارد شود")
    bank_account = models.CharField("شماره شبا", max_length=34, blank=True)
    hired_on = models.DateField("تاریخ شروع همکاری")
    left_on = models.DateField("تاریخ پایان همکاری", null=True, blank=True)
    is_active = models.BooleanField("فعال", default=True)

    class Meta:
        ordering = ["personnel_no"]
        verbose_name = "پرونده مالی کارمند"
        verbose_name_plural = "پرونده مالی کارکنان"
        permissions = [("view_payroll", "مشاهده حقوق همه کارکنان")]

    def __str__(self):
        return f"{self.personnel_no} {self.party.name}"


class ProfilePayItem(models.Model):
    profile = models.ForeignKey(PayrollProfile, on_delete=models.CASCADE, related_name="fixed_items", verbose_name="کارمند")
    item_type = models.ForeignKey(PayItemType, on_delete=models.PROTECT, related_name="+", verbose_name="قلم",
                                  limit_choices_to={"calc": "profile"})
    amount = models.PositiveBigIntegerField("مبلغ ماهانه (ریال)")

    class Meta:
        verbose_name = "قلم ثابت کارمند"
        verbose_name_plural = "اقلام ثابت ماهانه"
        constraints = [models.UniqueConstraint(fields=["profile", "item_type"], name="unique_profile_item")]


class EmployeeLoan(PostedDocument):
    """وام یا مساعده قسطی کارمند؛ با ثبت نهایی پرداخت می‌شود و اقساط در حقوق ماهانه کسر می‌شود."""

    NUMBER_PREFIX = "LON"

    profile = models.ForeignKey(PayrollProfile, on_delete=models.PROTECT, related_name="loans", verbose_name="کارمند")
    amount = models.PositiveBigIntegerField("مبلغ (ریال)")
    installment = models.PositiveBigIntegerField("مبلغ هر قسط (ریال)")
    method = models.CharField("روش پرداخت", max_length=8, choices=[("cash", "نقد"), ("bank", "انتقال بانکی")], default="bank")
    money_account = models.ForeignKey(Account, on_delete=models.PROTECT, null=True, blank=True, related_name="+",
                                      verbose_name="صندوق / بانک", limit_choices_to={"is_cash": True})
    reference_no = models.CharField("شماره پیگیری", max_length=64, blank=True)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "وام / مساعده کارمند"
        verbose_name_plural = "وام و مساعده کارکنان"
        permissions = [("post_employeeloan", "پرداخت وام کارمند"), ("reverse_employeeloan", "برگشت وام کارمند")]
        constraints = [models.CheckConstraint(condition=models.Q(installment__gt=0) & models.Q(installment__lte=models.F("amount")),
                                              name="loan_installment_range")]

    def validate_for_post(self):
        from ..services import payroll

        payroll.validate_loan(self)

    def perform_post(self, user):
        from ..services import payroll

        payroll.post_loan(self, user)

    def validate_for_reverse(self):
        from ..services import payroll

        payroll.validate_loan_reverse(self)

    def perform_reverse(self, user):
        from apps.accounting import services as acc

        acc.reverse_entry_for(self, user)


class PayrollRun(PostedDocument):
    """لیست حقوق یک ماه: محاسبه ← تأیید (کاربر دیگر) ← ثبت نهایی (سند تعهد حقوق، بیمه و مالیات)."""

    NUMBER_PREFIX = "PRL"

    year = models.PositiveSmallIntegerField("سال (شمسی)")
    month = models.PositiveSmallIntegerField("ماه (شمسی)")
    calculated_at = models.DateTimeField("زمان محاسبه", null=True, blank=True, editable=False)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name="+", verbose_name="تأییدکننده", editable=False)
    approved_at = models.DateTimeField("زمان تأیید", null=True, blank=True, editable=False)

    period_active = unique_when(~models.Q(status="reversed"))

    class Meta:
        ordering = ["-year", "-month"]
        verbose_name = "لیست حقوق ماهانه"
        verbose_name_plural = "لیست‌های حقوق"
        permissions = [("approve_payrollrun", "تأیید لیست حقوق"), ("post_payrollrun", "ثبت نهایی لیست حقوق"),
                       ("reverse_payrollrun", "برگشت لیست حقوق")]
        constraints = [
            models.UniqueConstraint(fields=["year", "month", "period_active"], name="unique_payroll_month"),
            models.CheckConstraint(condition=models.Q(month__gte=1, month__lte=12), name="payroll_month_range"),
        ]

    def __str__(self):
        return self.number or f"حقوق {self.year}/{self.month:02d}"

    def validate_for_post(self):
        from ..services import payroll

        payroll.validate_run_post(self)

    def perform_post(self, user):
        from ..services import payroll

        payroll.post_run(self, user)

    def perform_reverse(self, user):
        from apps.accounting import services as acc

        acc.reverse_entry_for(self, user)


class PayrollLine(DocumentLine):
    DOCUMENT_FIELD = "run"

    run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name="lines", verbose_name="لیست حقوق")
    profile = models.ForeignKey(PayrollProfile, on_delete=models.PROTECT, related_name="payroll_lines", verbose_name="کارمند")
    work_days = models.PositiveSmallIntegerField("روزهای کارکرد")
    absence_days = models.PositiveSmallIntegerField("روزهای غیبت (بدون حقوق)", default=0)
    leave_days = models.PositiveSmallIntegerField("روزهای مرخصی با حقوق", default=0)
    overtime_hours = models.DecimalField("ساعت اضافه‌کاری", max_digits=6, decimal_places=2, default=0)
    bonus = models.PositiveBigIntegerField("پاداش (ریال)", default=0)
    commission = models.PositiveBigIntegerField("پورسانت (ریال)", default=0)
    advance_deduction = models.PositiveBigIntegerField("کسر مساعده (ریال)", default=0)
    other_deduction = models.PositiveBigIntegerField("سایر کسور (ریال)", default=0)
    gross = models.PositiveBigIntegerField("ناخالص (ریال)", default=0, editable=False)
    insurable = models.PositiveBigIntegerField("مشمول بیمه (ریال)", default=0, editable=False)
    taxable = models.PositiveBigIntegerField("مشمول مالیات (ریال)", default=0, editable=False)
    deductions = models.PositiveBigIntegerField("جمع کسور (ریال)", default=0, editable=False)
    employer_cost = models.PositiveBigIntegerField("سهم کارفرما (ریال)", default=0, editable=False)
    net = models.PositiveBigIntegerField("خالص پرداختی (ریال)", default=0, editable=False)
    warnings = models.CharField("هشدار محاسبه", max_length=300, blank=True, editable=False)

    class Meta:
        ordering = ["profile__personnel_no"]
        verbose_name = "ردیف حقوق"
        verbose_name_plural = "ردیف‌های حقوق"
        constraints = [models.UniqueConstraint(fields=["run", "profile"], name="unique_payroll_line")]

    def __str__(self):
        return f"{self.profile} — {self.net:,}"


class PayrollItem(DocumentLine):
    DOCUMENT_FIELD = "run"

    run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name="items", verbose_name="لیست حقوق")
    line = models.ForeignKey(PayrollLine, on_delete=models.CASCADE, related_name="items", verbose_name="ردیف")
    item_type = models.ForeignKey(PayItemType, on_delete=models.PROTECT, related_name="+", verbose_name="قلم")
    loan = models.ForeignKey(EmployeeLoan, on_delete=models.PROTECT, null=True, blank=True, related_name="installments",
                             verbose_name="وام")
    amount = models.PositiveBigIntegerField("مبلغ (ریال)")
    detail = models.CharField("جزئیات محاسبه", max_length=200, blank=True)

    class Meta:
        ordering = ["item_type__order", "id"]
        verbose_name = "قلم حقوق ماه"
        verbose_name_plural = "اقلام حقوق ماه"

from django.conf import settings
from django.db import models
from django.db.models import Sum

from apps.accounting.models import Party, PaymentMethod
from apps.audit.documents import DocStatus, DocumentError, DocumentLine, PostedDocument
from apps.inventory.models import Batch, Location, Warehouse
from apps.shop.models import Order, Product


class CashRegister(models.Model):
    name = models.CharField("نام صندوق", max_length=80, unique=True)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="+", verbose_name="انبار فروش")
    is_active = models.BooleanField("فعال", default=True)

    class Meta:
        verbose_name = "صندوق"
        verbose_name_plural = "صندوق‌ها"

    def __str__(self):
        return self.name


class CashSession(models.Model):
    """شیفت صندوق: باز کردن با موجودی اولیه، بستن با شمارش نقد و ثبت کسری/اضافه."""

    class Status(models.TextChoices):
        OPEN = "open", "باز"
        CLOSED = "closed", "بسته"

    register = models.ForeignKey(CashRegister, on_delete=models.PROTECT, related_name="sessions", verbose_name="صندوق")
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name="صندوق‌دار")
    status = models.CharField("وضعیت", max_length=6, choices=Status.choices, default=Status.OPEN, editable=False)
    opened_at = models.DateTimeField("زمان باز شدن", auto_now_add=True)
    opening_cash = models.PositiveBigIntegerField("موجودی اولیه نقد (ریال)", default=0)
    closed_at = models.DateTimeField("زمان بستن", null=True, blank=True, editable=False)
    counted_cash = models.PositiveBigIntegerField("نقد شمارش‌شده (ریال)", null=True, blank=True, editable=False)
    expected_cash = models.BigIntegerField("نقد مورد انتظار (ریال)", null=True, blank=True, editable=False)
    difference = models.BigIntegerField("اختلاف صندوق (ریال)", null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-opened_at"]
        verbose_name = "شیفت صندوق"
        verbose_name_plural = "شیفت‌های صندوق"
        constraints = [
            models.UniqueConstraint(fields=["register"], condition=models.Q(status="open"), name="one_open_session_per_register")
        ]

    def __str__(self):
        return f"{self.register} - {self.cashier} ({self.get_status_display()})"


class SalesInvoice(PostedDocument):
    NUMBER_PREFIX = "SI"

    class Channel(models.TextChoices):
        POS = "pos", "فروش حضوری"
        ONLINE = "online", "فروش آنلاین"

    channel = models.CharField("کانال فروش", max_length=8, choices=Channel.choices, default=Channel.POS)
    session = models.ForeignKey(CashSession, on_delete=models.PROTECT, null=True, blank=True, related_name="invoices", verbose_name="شیفت صندوق")
    order = models.OneToOneField(Order, on_delete=models.PROTECT, null=True, blank=True, related_name="invoice", verbose_name="سفارش آنلاین")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="+", verbose_name="انبار")
    customer = models.ForeignKey(Party, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name="مشتری")
    discount = models.PositiveBigIntegerField("تخفیف فاکتور (ریال)", default=0)
    insurer = models.ForeignKey(
        Party, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name="بیمه‌گر",
        limit_choices_to={"kind": Party.Kind.INSURER},
    )
    insurance_amount = models.PositiveBigIntegerField("سهم بیمه (ریال)", default=0)
    # نسخه
    prescription_number = models.CharField("شماره / کد رهگیری نسخه", max_length=40, blank=True)
    prescriber_name = models.CharField("نام پزشک تجویزکننده", max_length=120, blank=True)
    prescriber_council_no = models.CharField("شماره نظام پزشکی تجویزکننده", max_length=20, blank=True)
    pharmacist_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+",
        verbose_name="تأیید داروساز", editable=False,
    )
    client_token = models.CharField(
        "شناسه یکتای درخواست", max_length=40, unique=True, null=True, blank=True, editable=False,
        help_text="جلوی ثبت دوباره یک فروش با دوبار کلیک یا ارسال مجدد فرم را می‌گیرد.",
    )

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "فاکتور فروش"
        verbose_name_plural = "فاکتورهای فروش"
        permissions = [
            ("post_salesinvoice", "ثبت نهایی فاکتور فروش"),
            ("reverse_salesinvoice", "ابطال فاکتور فروش"),
            ("approve_prescription", "تأیید نسخه و فروش داروی نسخه‌ای (داروساز)"),
            ("use_pos", "کار با صندوق فروش"),
        ]

    def validate_for_post(self):
        from . import services

        services.validate_sale(self)

    def perform_post(self, user):
        from . import services

        services.post_sale(self, user)

    def validate_for_reverse(self):
        if self.returns.filter(status=DocStatus.POSTED).exists():
            raise DocumentError("برای این فاکتور مرجوعی ثبت شده و قابل ابطال نیست.")
        if self.channel == self.Channel.ONLINE:
            raise DocumentError("فاکتور سفارش آنلاین را با سند مرجوعی اصلاح کنید.")

    def perform_reverse(self, user):
        from . import services

        services.reverse_sale(self, user)

    @property
    def subtotal(self):
        return sum(line.line_total for line in self.lines.all())

    @property
    def tax(self):
        return sum(line.tax for line in self.lines.all())

    @property
    def total(self):
        return self.subtotal - self.discount + self.tax

    @property
    def paid(self):
        return self.payments.aggregate(s=Sum("amount"))["s"] or 0

    @property
    def needs_prescription(self):
        return self.lines.filter(product__requires_prescription=True).exists()


class SalesInvoiceLine(DocumentLine):
    document = models.ForeignKey(SalesInvoice, on_delete=models.CASCADE, related_name="lines", verbose_name="فاکتور")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="+", verbose_name="کالا")
    quantity = models.PositiveIntegerField("تعداد (واحد پایه)")
    unit_price = models.PositiveBigIntegerField("قیمت واحد (ریال)")
    line_discount = models.PositiveBigIntegerField("تخفیف ردیف (ریال)", default=0)
    tax = models.PositiveBigIntegerField("مالیات (ریال)", default=0, editable=False)
    cost_total = models.BigIntegerField("بهای تمام‌شده (ریال)", default=0, editable=False)

    class Meta:
        verbose_name = "ردیف فاکتور فروش"
        verbose_name_plural = "ردیف‌های فاکتور فروش"

    def __str__(self):
        return f"{self.product} × {self.quantity}"

    @property
    def line_total(self):
        return self.quantity * self.unit_price - self.line_discount

    @property
    def returned_quantity(self):
        return (
            self.return_lines.filter(document__status=DocStatus.POSTED).aggregate(q=Sum("quantity"))["q"] or 0
        )


class SalesAllocation(models.Model):
    """کدام بچ و محل، با چه بهایی، برای هر ردیف فروش خارج شد (برای مرجوعی و فراخوان)."""

    line = models.ForeignKey(SalesInvoiceLine, on_delete=models.PROTECT, related_name="allocations", verbose_name="ردیف")
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, related_name="+", verbose_name="بچ")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+", verbose_name="محل")
    quantity = models.PositiveIntegerField("تعداد")
    cost_total = models.BigIntegerField("بها (ریال)")
    returned_quantity = models.PositiveIntegerField("تعداد مرجوع‌شده", default=0)
    returned_cost = models.BigIntegerField("بهای مرجوع‌شده (ریال)", default=0)

    class Meta:
        verbose_name = "تخصیص بچ فروش"
        verbose_name_plural = "تخصیص بچ‌های فروش"


class SalesPayment(DocumentLine):
    class Method(models.TextChoices):
        CASH = PaymentMethod.CASH.value, "نقد"
        CARD = PaymentMethod.CARD.value, "کارتخوان"
        CREDIT = "credit", "نسیه (حساب مشتری)"

    document = models.ForeignKey(SalesInvoice, on_delete=models.CASCADE, related_name="payments", verbose_name="فاکتور")
    method = models.CharField("روش", max_length=8, choices=Method.choices)
    amount = models.PositiveBigIntegerField("مبلغ (ریال)")
    reference_no = models.CharField("شماره پیگیری", max_length=40, blank=True)

    class Meta:
        verbose_name = "پرداخت فاکتور"
        verbose_name_plural = "پرداخت‌های فاکتور"

    def __str__(self):
        return f"{self.get_method_display()}: {self.amount}"


class SalesReturn(PostedDocument):
    """مرجوعی فروش؛ کالا به همان بچ (اگر سالم) یا انبار قرنطینه برمی‌گردد."""

    NUMBER_PREFIX = "SR"

    class Refund(models.TextChoices):
        CASH = PaymentMethod.CASH.value, "نقد"
        CARD = PaymentMethod.CARD.value, "کارتخوان"
        CREDIT = "credit", "بستانکار کردن حساب مشتری"

    invoice = models.ForeignKey(SalesInvoice, on_delete=models.PROTECT, related_name="returns", verbose_name="فاکتور فروش")
    reason = models.CharField("علت مرجوعی", max_length=300)
    restock = models.BooleanField(
        "قابل فروش مجدد", default=False,
        help_text="فقط اگر کالا پلمب، سالم و با شرایط نگهداری درست برگشته. در غیر این صورت به انبار قرنطینه می‌رود.",
    )
    refund_method = models.CharField("روش استرداد", max_length=8, choices=Refund.choices, default=Refund.CASH)
    session = models.ForeignKey(CashSession, on_delete=models.PROTECT, null=True, blank=True, related_name="returns", verbose_name="شیفت صندوق")
    refund_amount = models.BigIntegerField("مبلغ استرداد (ریال)", default=0, editable=False)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "مرجوعی فروش"
        verbose_name_plural = "مرجوعی‌های فروش"
        permissions = [("post_salesreturn", "ثبت نهایی مرجوعی")]

    def validate_for_post(self):
        from . import services

        services.validate_return(self)

    def perform_post(self, user):
        from . import services

        services.post_return(self, user)

    def validate_for_reverse(self):
        raise DocumentError("مرجوعی ثبت‌شده قابل برگشت نیست؛ در صورت اشتباه فاکتور فروش جدید صادر کنید.")


class SalesReturnLine(DocumentLine):
    document = models.ForeignKey(SalesReturn, on_delete=models.CASCADE, related_name="lines", verbose_name="مرجوعی")
    invoice_line = models.ForeignKey(SalesInvoiceLine, on_delete=models.PROTECT, related_name="return_lines", verbose_name="ردیف فاکتور")
    quantity = models.PositiveIntegerField("تعداد")

    class Meta:
        verbose_name = "ردیف مرجوعی"
        verbose_name_plural = "ردیف‌های مرجوعی"

    def __str__(self):
        return f"{self.invoice_line.product} × {self.quantity}"

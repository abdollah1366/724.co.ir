from django.db import models
from django.db.models import Sum

from apps.accounting.models import Party
from apps.audit.documents import DocumentError, DocumentLine, PostedDocument
from apps.inventory.models import Location, Warehouse
from apps.shop.models import Product

SUPPLIERS = {"kind": Party.Kind.SUPPLIER}


class PurchaseOrder(PostedDocument):
    """سفارش خرید؛ ثبت نهایی آن (= تأیید) اثری بر موجودی و حساب‌ها ندارد."""

    NUMBER_PREFIX = "PO"

    supplier = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="+", verbose_name="تأمین‌کننده", limit_choices_to=SUPPLIERS)
    expected_date = models.DateField("تاریخ تحویل مورد انتظار", null=True, blank=True)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "سفارش خرید"
        verbose_name_plural = "سفارش‌های خرید"
        permissions = [("post_purchaseorder", "تأیید سفارش خرید"), ("reverse_purchaseorder", "لغو سفارش خرید تأییدشده")]

    def validate_for_post(self):
        if not self.lines.exists():
            raise DocumentError("سفارش ردیف ندارد.")
        if self.supplier.kind != Party.Kind.SUPPLIER:
            raise DocumentError("طرف حساب سفارش باید تأمین‌کننده باشد.")

    def perform_post(self, user):
        pass

    def validate_for_reverse(self):
        if self.receipts.filter(status=PostedDocument.Status.POSTED).exists():
            raise DocumentError("برای این سفارش رسید ثبت شده؛ ابتدا رسیدها را برگشت بزنید.")

    def perform_reverse(self, user):
        pass

    @property
    def total(self):
        return sum(line.quantity * line.unit_price for line in self.lines.all())


class PurchaseOrderLine(DocumentLine):
    document = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines", verbose_name="سفارش")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="+", verbose_name="کالا")
    quantity = models.PositiveIntegerField("تعداد (واحد پایه)")
    unit_price = models.PositiveBigIntegerField("قیمت واحد (ریال)")

    class Meta:
        verbose_name = "ردیف سفارش خرید"
        verbose_name_plural = "ردیف‌های سفارش خرید"

    def __str__(self):
        return f"{self.product} × {self.quantity}"

    @property
    def received_quantity(self):
        return (
            self.receipt_lines.filter(document__status=PostedDocument.Status.POSTED).aggregate(q=Sum("quantity"))["q"] or 0
        )


class GoodsReceipt(PostedDocument):
    """رسید انبار خرید: ورود کالا با بچ، انقضا، محل نگهداری و بررسی اصالت."""

    NUMBER_PREFIX = "GR"

    supplier = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="+", verbose_name="تأمین‌کننده", limit_choices_to=SUPPLIERS)
    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.PROTECT, null=True, blank=True, related_name="receipts", verbose_name="سفارش خرید"
    )
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="+", verbose_name="انبار")
    delivery_note = models.CharField("شماره حواله / بارنامه تأمین‌کننده", max_length=40, blank=True)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "رسید انبار خرید"
        verbose_name_plural = "رسیدهای انبار خرید"
        permissions = [("post_goodsreceipt", "ثبت نهایی رسید انبار"), ("reverse_goodsreceipt", "برگشت رسید انبار")]

    def validate_for_post(self):
        from . import services

        services.validate_receipt(self)

    def perform_post(self, user):
        from . import services

        services.post_receipt(self, user)

    def validate_for_reverse(self):
        if self.invoices.filter(status=PostedDocument.Status.POSTED).exists():
            raise DocumentError("برای این رسید فاکتور خرید ثبت شده؛ ابتدا فاکتور را برگشت بزنید.")

    def perform_reverse(self, user):
        from . import services

        services.reverse_receipt(self, user)

    @property
    def total(self):
        return sum(line.quantity * line.unit_cost for line in self.lines.all())


class GoodsReceiptLine(DocumentLine):
    document = models.ForeignKey(GoodsReceipt, on_delete=models.CASCADE, related_name="lines", verbose_name="رسید")
    po_line = models.ForeignKey(
        PurchaseOrderLine, on_delete=models.PROTECT, null=True, blank=True, related_name="receipt_lines", verbose_name="ردیف سفارش"
    )
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="+", verbose_name="کالا")
    batch_number = models.CharField("شماره بچ / سریال", max_length=60, blank=True)
    manufacture_date = models.DateField("تاریخ تولید", null=True, blank=True)
    expiry_date = models.DateField("تاریخ انقضا", null=True, blank=True)
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+", verbose_name="محل نگهداری")
    quantity = models.PositiveIntegerField("تعداد (واحد پایه)")
    unit_cost = models.PositiveBigIntegerField("بهای واحد (ریال)")
    authenticity_code = models.CharField("کد اصالت / رهگیری", max_length=80, blank=True)
    authenticity_verified = models.BooleanField("اصالت بررسی و تأیید شد", default=False)

    class Meta:
        verbose_name = "ردیف رسید"
        verbose_name_plural = "ردیف‌های رسید"

    def __str__(self):
        return f"{self.product} × {self.quantity}"


class PurchaseInvoice(PostedDocument):
    """فاکتور خرید تأمین‌کننده؛ بدهی ما به تأمین‌کننده را ثبت می‌کند."""

    NUMBER_PREFIX = "PI"

    supplier = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="+", verbose_name="تأمین‌کننده", limit_choices_to=SUPPLIERS)
    receipt = models.ForeignKey(GoodsReceipt, on_delete=models.PROTECT, related_name="invoices", verbose_name="رسید انبار")
    supplier_invoice_no = models.CharField("شماره فاکتور تأمین‌کننده", max_length=40)
    discount = models.PositiveBigIntegerField("تخفیف (ریال)", default=0)
    tax = models.PositiveBigIntegerField("مالیات بر ارزش افزوده (ریال)", default=0)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "فاکتور خرید"
        verbose_name_plural = "فاکتورهای خرید"
        permissions = [("post_purchaseinvoice", "ثبت نهایی فاکتور خرید"), ("reverse_purchaseinvoice", "برگشت فاکتور خرید")]
        constraints = [
            # جلوگیری از ثبت دوباره یک فاکتور تأمین‌کننده
            models.UniqueConstraint(
                fields=["supplier", "supplier_invoice_no"], condition=~models.Q(status="reversed"),
                name="unique_supplier_invoice",
            )
        ]

    def validate_for_post(self):
        from . import services

        services.validate_invoice(self)

    def perform_post(self, user):
        from . import services

        services.post_invoice(self, user)

    def perform_reverse(self, user):
        from apps.accounting import services as acc

        acc.reverse_entry_for(self, user)

    @property
    def subtotal(self):
        return sum(line.quantity * line.unit_price for line in self.lines.all())

    @property
    def total(self):
        return self.subtotal - self.discount + self.tax


class PurchaseInvoiceLine(DocumentLine):
    document = models.ForeignKey(PurchaseInvoice, on_delete=models.CASCADE, related_name="lines", verbose_name="فاکتور")
    receipt_line = models.ForeignKey(GoodsReceiptLine, on_delete=models.PROTECT, related_name="+", verbose_name="ردیف رسید")
    quantity = models.PositiveIntegerField("تعداد")
    unit_price = models.PositiveBigIntegerField("قیمت واحد فاکتور (ریال)")

    class Meta:
        verbose_name = "ردیف فاکتور خرید"
        verbose_name_plural = "ردیف‌های فاکتور خرید"
        constraints = [models.UniqueConstraint(fields=["document", "receipt_line"], name="unique_invoice_receipt_line")]

    def __str__(self):
        return f"{self.receipt_line.product} × {self.quantity}"

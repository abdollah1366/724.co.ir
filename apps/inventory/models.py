from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.audit.documents import DocumentError, DocumentLine, ImmutableRecord, PostedDocument
from apps.shop.models import Product, StorageCondition


class Warehouse(models.Model):
    """انبار یا بخش نگهداری: داروخانه مرکزی، انبار دپو، انبار بخش‌ها (کلینیک، پاراکلینیک، اتاق پانسمان، ...)، قرنطینه."""

    class Kind(models.TextChoices):
        CENTRAL = "central", "داروخانه مرکزی (فروش)"
        DEPOT = "depot", "انبار دپو / ذخیره"
        WARD = "ward", "انبار بخش (کلینیک، پاراکلینیک، اتاق پانسمان، ...)"
        QUARANTINE = "quarantine", "قرنطینه، مرجوعی و ضایعات"

    code = models.CharField("کد انبار", max_length=20, unique=True)
    name = models.CharField("نام انبار", max_length=120)
    kind = models.CharField("نوع", max_length=12, choices=Kind.choices, default=Kind.CENTRAL)
    is_sellable = models.BooleanField(
        "فروش از این انبار مجاز است", default=True,
        help_text="انبار قرنطینه، مرجوعی و ضایعات را غیرقابل‌فروش تعریف کنید.",
    )
    supplies_from = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="supplied", verbose_name="تأمین از انبار",
        help_text="درخواست کالای این بخش پیش‌فرض به این انبار می‌رود (مثلاً اتاق پانسمان ← داروخانه مرکزی).",
    )
    cost_center = models.ForeignKey(
        "accounting.CostCenter", on_delete=models.PROTECT, null=True, blank=True, related_name="+",
        verbose_name="مرکز هزینه", help_text="مصرف داخلی این بخش به این مرکز هزینه ثبت می‌شود.",
    )
    keepers = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="kept_warehouses", verbose_name="مسئولان بخش / انبار",
        help_text="کاربرانی که برای این بخش درخواست کالا و مصرف ثبت می‌کنند. خالی = همه انبارداران.",
    )
    is_active = models.BooleanField("فعال", default=True)

    class Meta:
        ordering = ["kind", "code"]
        verbose_name = "انبار"
        verbose_name_plural = "انبارها"

    def __str__(self):
        return self.name


class Location(models.Model):
    """محل نگهداری داخل انبار: قفسه، کشو، یخچال."""

    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="locations", verbose_name="انبار")
    code = models.CharField("کد محل", max_length=20)
    name = models.CharField("نام محل", max_length=120)
    storage_condition = models.CharField(
        "شرایط نگهداری", max_length=8, choices=StorageCondition.choices, default=StorageCondition.ROOM
    )
    is_active = models.BooleanField("فعال", default=True)

    class Meta:
        ordering = ["warehouse", "code"]
        verbose_name = "محل نگهداری"
        verbose_name_plural = "محل‌های نگهداری"
        constraints = [models.UniqueConstraint(fields=["warehouse", "code"], name="unique_location_code")]

    def __str__(self):
        return f"{self.warehouse} / {self.name}"


class Batch(models.Model):
    """بچ (سری ساخت) یا شماره سریال. برای کالای بدون ردیابی بچ، بچ عمومی ساخته می‌شود."""

    GENERIC_NUMBER = "-"

    class Status(models.TextChoices):
        ACTIVE = "active", "سالم و قابل فروش"
        QUARANTINE = "quarantine", "قرنطینه"
        RECALLED = "recalled", "فراخوان‌شده"
        BLOCKED = "blocked", "توقیف / غیرقابل فروش"
        EXPIRED = "expired", "منقضی‌شده"

    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="batches", verbose_name="کالا")
    number = models.CharField("شماره بچ / سریال", max_length=60)
    expiry_date = models.DateField("تاریخ انقضا", null=True, blank=True)
    manufacture_date = models.DateField("تاریخ تولید", null=True, blank=True)
    status = models.CharField("وضعیت", max_length=12, choices=Status.choices, default=Status.ACTIVE, editable=False)
    created_at = models.DateTimeField("زمان ایجاد", auto_now_add=True)

    class Meta:
        ordering = ["product", "expiry_date", "number"]
        verbose_name = "بچ / سریال"
        verbose_name_plural = "بچ‌ها و سریال‌ها"
        permissions = [("change_batch_status", "قرنطینه، فراخوان و آزادسازی بچ")]
        constraints = [models.UniqueConstraint(fields=["product", "number"], name="unique_batch_number")]

    def __str__(self):
        return f"{self.product} - {self.number}"

    @property
    def is_expired(self):
        return bool(self.expiry_date and self.expiry_date < timezone.localdate())

    @property
    def is_sellable(self):
        return self.status == self.Status.ACTIVE and not self.is_expired


class BatchStatusChange(ImmutableRecord):
    """سابقه قرنطینه، فراخوان، توقیف و آزادسازی بچ‌ها."""

    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, related_name="status_changes", verbose_name="بچ")
    old_status = models.CharField("وضعیت قبلی", max_length=12, choices=Batch.Status.choices)
    new_status = models.CharField("وضعیت جدید", max_length=12, choices=Batch.Status.choices)
    reason = models.CharField("علت", max_length=300)
    reference = models.CharField("شماره مرجع / بخشنامه", max_length=60, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name="کاربر"
    )
    created_at = models.DateTimeField("زمان", auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "تغییر وضعیت بچ"
        verbose_name_plural = "سابقه وضعیت بچ‌ها (فراخوان، قرنطینه)"

    def __str__(self):
        return f"{self.batch}: {self.get_old_status_display()} ← {self.get_new_status_display()}"


class StockBalance(models.Model):
    """مانده لحظه‌ای هر کالا به تفکیک انبار، محل و بچ. فقط سرویس انبار آن را تغییر می‌دهد."""

    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="balances", verbose_name="کالا")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="balances", verbose_name="انبار")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="balances", verbose_name="محل")
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, related_name="balances", verbose_name="بچ")
    quantity = models.IntegerField("مانده", default=0)

    class Meta:
        verbose_name = "مانده موجودی"
        verbose_name_plural = "مانده موجودی (انبار، محل، بچ)"
        constraints = [
            models.UniqueConstraint(fields=["product", "warehouse", "location", "batch"], name="unique_stock_balance"),
            models.CheckConstraint(condition=models.Q(quantity__gte=0), name="stock_balance_not_negative"),
        ]

    def __str__(self):
        return f"{self.product} @ {self.location} [{self.batch.number}]: {self.quantity}"


class StockMove(ImmutableRecord):
    """دفتر حرکات کالا (کاردکس). هر ورود و خروج با بهای واحد و میانگین موزون پس از حرکت ثبت می‌شود."""

    class Type(models.TextChoices):
        OPENING = "opening", "موجودی افتتاحیه"
        RECEIPT = "receipt", "رسید خرید"
        SALE = "sale", "فروش"
        SALE_RETURN = "sale_return", "برگشت از فروش"
        ADJUST_IN = "adjust_in", "اضافه انبارگردانی"
        ADJUST_OUT = "adjust_out", "کسری انبارگردانی"
        WASTE = "waste", "ضایعات / امحا"
        TRANSFER_OUT = "transfer_out", "انتقال (خروج)"
        TRANSFER_IN = "transfer_in", "انتقال (ورود)"
        CONSUME = "consume", "مصرف داخلی بخش"

    created_at = models.DateTimeField("زمان", auto_now_add=True, db_index=True)
    move_type = models.CharField("نوع حرکت", max_length=14, choices=Type.choices)
    is_reversal = models.BooleanField("برگشتی", default=False)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="moves", verbose_name="کالا")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="+", verbose_name="انبار")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+", verbose_name="محل")
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, related_name="moves", verbose_name="بچ")
    quantity = models.IntegerField("تعداد (+ورود / -خروج)")
    unit_cost = models.DecimalField("بهای واحد (ریال)", max_digits=20, decimal_places=4)
    total_cost = models.BigIntegerField("ارزش حرکت (ریال)")
    product_qty_after = models.IntegerField("مانده کل کالا پس از حرکت")
    avg_cost_after = models.DecimalField("میانگین موزون پس از حرکت", max_digits=20, decimal_places=4)
    source_type = models.CharField("نوع سند مرجع", max_length=60)
    source_id = models.PositiveBigIntegerField("شناسه سند مرجع")
    source_number = models.CharField("شماره سند مرجع", max_length=24, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name="کاربر"
    )

    class Meta:
        ordering = ["-id"]
        verbose_name = "حرکت کالا"
        verbose_name_plural = "حرکات کالا (کاردکس)"
        indexes = [models.Index(fields=["source_type", "source_id"])]
        constraints = [models.CheckConstraint(condition=~models.Q(quantity=0), name="stock_move_nonzero")]

    def __str__(self):
        return f"{self.get_move_type_display()} {self.product} {self.quantity:+d}"


class InventoryDocument(PostedDocument):
    """سند انبار: افتتاحیه، اضافه/کسری انبارگردانی، ضایعات و انتقال بین انبارها."""

    NUMBER_PREFIX = "INV"

    class Kind(models.TextChoices):
        OPENING = "opening", "موجودی افتتاحیه"
        ADJUST_IN = "adjust_in", "اضافه انبارگردانی"
        ADJUST_OUT = "adjust_out", "کسری انبارگردانی"
        WASTE = "waste", "ضایعات / امحا"
        TRANSFER = "transfer", "انتقال بین انبار / محل"
        CONSUME = "consume", "مصرف داخلی بخش"

    INBOUND = {Kind.OPENING, Kind.ADJUST_IN}

    kind = models.CharField("نوع سند", max_length=12, choices=Kind.choices)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="+", verbose_name="انبار")
    dest_warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name="انبار مقصد"
    )
    reason = models.CharField("علت", max_length=300, blank=True)
    stock_request = models.ForeignKey(
        "StockRequest", on_delete=models.PROTECT, null=True, blank=True, related_name="issues", editable=False,
        verbose_name="درخواست کالا",
    )

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "سند انبار"
        verbose_name_plural = "اسناد انبار"
        permissions = [("post_inventorydocument", "ثبت نهایی سند انبار"), ("reverse_inventorydocument", "برگشت سند انبار"),
                       ("consume_inventorydocument", "ثبت مصرف داخلی بخش"),
                       ("count_inventorydocument", "انبارگردانی (شمارش و ثبت اختلاف)")]

    def validate_for_post(self):
        from . import services

        services.validate_inventory_document(self)

    def perform_post(self, user):
        from . import services

        services.post_inventory_document(self, user)

    def perform_reverse(self, user):
        from . import services

        services.reverse_inventory_document(self, user)


class InventoryDocumentLine(DocumentLine):
    document = models.ForeignKey(InventoryDocument, on_delete=models.CASCADE, related_name="lines", verbose_name="سند")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="+", verbose_name="کالا")
    batch_number = models.CharField("شماره بچ / سریال", max_length=60, blank=True)
    expiry_date = models.DateField("تاریخ انقضا", null=True, blank=True)
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+", verbose_name="محل")
    dest_location = models.ForeignKey(
        Location, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name="محل مقصد"
    )
    quantity = models.PositiveIntegerField("تعداد (واحد پایه)")
    unit_cost = models.PositiveBigIntegerField(
        "بهای واحد (ریال)", null=True, blank=True, help_text="فقط برای افتتاحیه و اضافه انبار؛ خروج با میانگین موزون."
    )

    class Meta:
        verbose_name = "ردیف سند انبار"
        verbose_name_plural = "ردیف‌های سند انبار"

    def __str__(self):
        return f"{self.product} × {self.quantity}"


class StockRequest(PostedDocument):
    """درخواست کالا از یک بخش (مثلاً اتاق پانسمان) به انبار تأمین (داروخانه مرکزی یا دپو).

    ثبت نهایی = ارسال درخواست. انباردار با «تحویل» سند انتقال می‌سازد که بچ‌ها را به ترتیب نزدیک‌ترین انقضا برمی‌دارد.
    """

    NUMBER_PREFIX = "SRQ"

    class Progress(models.TextChoices):
        OPEN = "open", "در انتظار تحویل"
        PARTIAL = "partial", "تحویل ناقص"
        DONE = "done", "تحویل کامل"
        CLOSED = "closed", "بسته‌شده (بدون تحویل کامل)"

    requester = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="requests", verbose_name="بخش درخواست‌کننده")
    source = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="incoming_requests", verbose_name="انبار تأمین")
    progress = models.CharField("وضعیت تحویل", max_length=8, choices=Progress.choices, default=Progress.OPEN, editable=False)
    reason = models.CharField("توضیح / علت نیاز", max_length=300, blank=True)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "درخواست کالا از بخش"
        verbose_name_plural = "درخواست‌های کالا از بخش‌ها"
        permissions = [("post_stockrequest", "ارسال درخواست کالا"), ("reverse_stockrequest", "ابطال درخواست کالا"),
                       ("issue_stockrequest", "تحویل درخواست کالا (انباردار)")]

    def validate_for_post(self):
        from . import requests

        requests.validate_request(self)

    def perform_post(self, user):
        pass

    def perform_reverse(self, user):
        pass

    def validate_for_reverse(self):
        if self.issues.filter(status="posted").exists():
            raise DocumentError("برای این درخواست کالا تحویل شده؛ ابتدا سند انتقال را برگشت بزنید.")


class StockRequestLine(DocumentLine):
    document = models.ForeignKey(StockRequest, on_delete=models.CASCADE, related_name="lines", verbose_name="درخواست")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="+", verbose_name="کالا")
    quantity = models.PositiveIntegerField("تعداد درخواستی (واحد پایه)")
    issued_quantity = models.PositiveIntegerField("تعداد تحویل‌شده", default=0, editable=False)

    class Meta:
        verbose_name = "ردیف درخواست کالا"
        verbose_name_plural = "ردیف‌های درخواست کالا"

    def __str__(self):
        return f"{self.product} × {self.quantity}"

    @property
    def remaining(self):
        return max(self.quantity - self.issued_quantity, 0)

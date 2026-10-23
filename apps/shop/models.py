import secrets

from django.conf import settings
from django.db import models, transaction
from django.db.models import F
from django.urls import reverse

from apps.core.validators import validate_mobile


class ProductKind(models.TextChoices):
    DRUG = "drug", "دارو"
    EQUIPMENT = "equipment", "تجهیزات پزشکی"
    COSMETIC = "cosmetic", "آرایشی و بهداشتی"


class StorageCondition(models.TextChoices):
    ROOM = "room", "دمای اتاق (زیر ۲۵ درجه)"
    COOL = "cool", "یخچال (۲ تا ۸ درجه)"
    FROZEN = "frozen", "فریزر (زیر صفر)"


class DeviceClass(models.TextChoices):
    I = "I", "کلاس I"
    IIA = "IIa", "کلاس IIa"
    IIB = "IIb", "کلاس IIb"
    III = "III", "کلاس III"


class Unit(models.Model):
    name = models.CharField("نام واحد", max_length=40, unique=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "واحد شمارش"
        verbose_name_plural = "واحدهای شمارش"

    def __str__(self):
        return self.name


class Category(models.Model):
    name = models.CharField("نام دسته", max_length=120)
    slug = models.SlugField("نامک", unique=True, allow_unicode=True)
    kind = models.CharField("نوع", max_length=12, choices=ProductKind.choices)
    order = models.PositiveSmallIntegerField("ترتیب", default=0)

    class Meta:
        ordering = ["kind", "order", "name"]
        verbose_name = "دسته‌بندی"
        verbose_name_plural = "دسته‌بندی‌ها"

    def __str__(self):
        return self.name


class ProductQuerySet(models.QuerySet):
    def available(self):
        return self.filter(is_active=True)


class Product(models.Model):
    Kind = ProductKind

    kind = models.CharField("نوع", max_length=12, choices=ProductKind.choices, default=ProductKind.DRUG)
    name = models.CharField("نام تجاری / نام محصول", max_length=200)
    slug = models.SlugField("نامک", unique=True, allow_unicode=True, max_length=220)
    category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="products", verbose_name="دسته‌بندی"
    )
    brand = models.CharField("برند / شرکت سازنده", max_length=120, blank=True)
    image = models.ImageField("تصویر", upload_to="products/", blank=True)
    short_description = models.CharField("توضیح کوتاه", max_length=300, blank=True)
    description = models.TextField("توضیحات", blank=True)
    price = models.PositiveBigIntegerField("قیمت (ریال)", null=True, blank=True)
    stock = models.PositiveIntegerField(
        "موجودی قابل فروش", default=0, editable=False,
        help_text="موجودی سالم انبارهای فروش منهای رزرو سفارش‌های آنلاین. فقط با اسناد انبار، خرید و فروش تغییر می‌کند.",
    )
    requires_prescription = models.BooleanField(
        "نیازمند نسخه پزشک",
        default=False,
        help_text="داروهای نسخه‌ای به‌صورت آنلاین فروخته نمی‌شوند و فقط با نسخه در داروخانه تحویل داده می‌شوند.",
    )
    is_active = models.BooleanField("فعال", default=True)

    # کدها، واحد و ردیابی (سامانه مدیریت داروخانه)
    sku = models.CharField("کد کالا", max_length=30, unique=True, null=True, blank=True)
    irc = models.CharField(
        "کد IRC / شناسه فرآورده", max_length=30, blank=True,
        help_text="کد ثبت فرآورده در سازمان غذا و دارو (برای دارو و آرایشی-بهداشتی).",
    )
    imed_code = models.CharField(
        "کد اداره کل تجهیزات پزشکی", max_length=40, blank=True, help_text="برای تجهیزات پزشکی دارای شناسه IMED."
    )
    device_class = models.CharField("کلاس خطر تجهیزات", max_length=4, choices=DeviceClass.choices, blank=True)
    base_unit = models.ForeignKey(
        Unit, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name="واحد پایه",
        help_text="موجودی و قیمت همیشه بر حسب این واحد است (مثلاً عدد یا ورق).",
    )
    storage_condition = models.CharField(
        "شرایط نگهداری", max_length=8, choices=StorageCondition.choices, default=StorageCondition.ROOM
    )
    track_batch = models.BooleanField("ردیابی بچ (سری ساخت)", default=True)
    track_expiry = models.BooleanField("ردیابی تاریخ انقضا", default=True)
    track_serial = models.BooleanField("ردیابی شماره سریال", default=False, help_text="برای دستگاه‌ها؛ هر سریال یک عدد.")
    requires_authenticity_check = models.BooleanField(
        "بررسی اصالت هنگام ورود", default=True, help_text="کد اصالت/رهگیری (مثل برچسب اصالت) هنگام رسید انبار الزامی است."
    )
    is_controlled = models.BooleanField(
        "داروی کنترل‌شده", default=False, help_text="داروهای تحت کنترل ویژه؛ فقط حضوری، با نسخه و سقف تعداد."
    )
    max_qty_per_sale = models.PositiveIntegerField("سقف تعداد در هر فروش", null=True, blank=True)
    reorder_point = models.PositiveIntegerField("نقطه سفارش", default=0, help_text="کمتر از این مقدار هشدار کمبود می‌دهد.")
    tax_rate = models.DecimalField("نرخ مالیات بر ارزش افزوده (٪)", max_digits=5, decimal_places=2, default=0)
    is_sellable = models.BooleanField("قابل فروش", default=True, help_text="با برداشتن تیک، فروش حضوری و آنلاین متوقف می‌شود.")
    avg_cost = models.DecimalField(
        "بهای تمام‌شده میانگین (ریال)", max_digits=20, decimal_places=4, default=0, editable=False
    )

    # اطلاعات علمی دارو
    generic_name = models.CharField("نام ژنریک", max_length=200, blank=True)
    dosage_form = models.CharField("شکل دارویی", max_length=100, blank=True, help_text="مثلاً قرص، شربت، کپسول")
    strength = models.CharField("مقدار ماده مؤثر", max_length=100, blank=True)
    mechanism = models.TextField("مکانیسم اثر", blank=True)
    indications = models.TextField("موارد مصرف", blank=True)
    dosage_note = models.TextField("نحوه مصرف", blank=True)
    side_effects = models.TextField("عوارض جانبی", blank=True)
    warnings = models.TextField("هشدارها و احتیاط‌ها", blank=True)
    interactions = models.TextField("تداخلات دارویی", blank=True)
    contraindications = models.TextField("موارد منع مصرف", blank=True)
    pregnancy_note = models.TextField("مصرف در بارداری و شیردهی", blank=True)

    # مشخصات تجهیزات
    specifications = models.TextField("مشخصات فنی", blank=True)
    warranty = models.CharField("گارانتی", max_length=200, blank=True)

    created_at = models.DateTimeField("زمان ایجاد", auto_now_add=True)
    updated_at = models.DateTimeField("آخرین تغییر", auto_now=True)

    objects = ProductQuerySet.as_manager()

    class Meta:
        ordering = ["name"]
        verbose_name = "محصول"
        verbose_name_plural = "محصولات"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("shop:product_detail", args=[self.slug])

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.kind == ProductKind.DRUG and not (self.track_batch and self.track_expiry):
            raise ValidationError("برای دارو ردیابی بچ و تاریخ انقضا الزامی است.")
        if self.track_serial and not self.track_batch:
            raise ValidationError("ردیابی سریال نیازمند ردیابی بچ است (هر سریال یک بچ است).")
        if self.is_controlled and not self.requires_prescription:
            raise ValidationError("داروی کنترل‌شده باید نیازمند نسخه باشد.")

    def save(self, *args, **kwargs):
        old_price = None
        if self.pk:
            old_price = Product.objects.filter(pk=self.pk).values_list("price", flat=True).first()
        super().save(*args, **kwargs)
        if self.price is not None and (old_price != self.price):
            PriceHistory.objects.create(product=self, price=self.price, previous_price=old_price)

    @property
    def in_stock(self):
        return self.stock > 0

    @property
    def can_order_online(self):
        return (
            self.is_active and self.is_sellable and not self.requires_prescription and not self.is_controlled
            and self.in_stock and bool(self.price)
        )


class ProductPackage(models.Model):
    """بسته‌بندی و بارکد: هر کالا می‌تواند چند بارکد با ضریب تبدیل متفاوت داشته باشد (عدد، ورق، جعبه)."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="packages", verbose_name="کالا")
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, verbose_name="واحد")
    factor = models.PositiveIntegerField("ضریب به واحد پایه", default=1)
    barcode = models.CharField("بارکد / GTIN", max_length=40, unique=True, null=True, blank=True)

    class Meta:
        verbose_name = "بسته‌بندی و بارکد"
        verbose_name_plural = "بسته‌بندی‌ها و بارکدها"
        constraints = [models.UniqueConstraint(fields=["product", "unit"], name="unique_package_unit")]

    def __str__(self):
        return f"{self.product} - {self.unit} ({self.factor})"


class PriceHistory(models.Model):
    """تاریخچه قیمت فروش؛ با هر تغییر قیمت کالا خودکار ثبت می‌شود."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="price_history", verbose_name="کالا")
    price = models.PositiveBigIntegerField("قیمت جدید (ریال)")
    previous_price = models.PositiveBigIntegerField("قیمت قبلی (ریال)", null=True, blank=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name="کاربر"
    )
    changed_at = models.DateTimeField("زمان", auto_now_add=True)

    class Meta:
        ordering = ["-changed_at", "-id"]
        verbose_name = "تاریخچه قیمت"
        verbose_name_plural = "تاریخچه قیمت‌ها"

    def __str__(self):
        return f"{self.product}: {self.price}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("تاریخچه قیمت قابل ویرایش نیست.")
        if self.changed_by_id is None:
            from apps.audit.context import get_actor

            self.changed_by = get_actor()[0]
        super().save(*args, **kwargs)


def generate_order_code():
    return "".join(secrets.choice("0123456789") for _ in range(8))


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "ثبت شده"
        CONFIRMED = "confirmed", "تأیید شده"
        READY = "ready", "آماده تحویل / ارسال"
        DELIVERED = "delivered", "تحویل شده"
        CANCELLED = "cancelled", "لغو شده"

    class Delivery(models.TextChoices):
        PICKUP = "pickup", "تحویل حضوری در داروخانه"
        COURIER = "courier", "ارسال با پیک"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="orders", verbose_name="کاربر")
    code = models.CharField("کد سفارش", max_length=8, unique=True, default=generate_order_code, editable=False)
    full_name = models.CharField("نام گیرنده", max_length=150)
    mobile = models.CharField("موبایل", max_length=11, validators=[validate_mobile])
    delivery_method = models.CharField("روش تحویل", max_length=10, choices=Delivery.choices, default=Delivery.PICKUP)
    address = models.TextField("آدرس", blank=True)
    note = models.CharField("توضیحات", max_length=500, blank=True)
    status = models.CharField("وضعیت", max_length=12, choices=Status.choices, default=Status.PENDING)
    total = models.PositiveBigIntegerField("مبلغ کل (ریال)", default=0)
    created_at = models.DateTimeField("زمان ثبت", auto_now_add=True)
    updated_at = models.DateTimeField("آخرین تغییر", auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "سفارش"
        verbose_name_plural = "سفارش‌ها"

    def __str__(self):
        return f"سفارش {self.code}"

    def save(self, *args, **kwargs):
        with transaction.atomic():
            previous = None
            if self.pk:
                previous = Order.objects.filter(pk=self.pk).values_list("status", flat=True).first()
            super().save(*args, **kwargs)
            if self.status == self.Status.DELIVERED and previous not in (None, self.Status.DELIVERED):
                # تحویل سفارش = فاکتور فروش آنلاین و خروج کالا از انبار (به ترتیب نزدیک‌ترین انقضا).
                from apps.sales.services import fulfill_online_order

                fulfill_online_order(self)

    @transaction.atomic
    def cancel(self):
        """لغو سفارش و برگرداندن موجودی."""
        if self.status in (self.Status.CANCELLED, self.Status.DELIVERED):
            return False
        for item in self.items.select_related("product"):
            if item.product_id:
                Product.objects.filter(pk=item.product_id).update(stock=F("stock") + item.quantity)
        self.status = self.Status.CANCELLED
        self.save(update_fields=["status", "updated_at"])
        return True


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items", verbose_name="سفارش")
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, verbose_name="محصول")
    product_name = models.CharField("نام محصول", max_length=200)
    unit_price = models.PositiveBigIntegerField("قیمت واحد (ریال)")
    quantity = models.PositiveSmallIntegerField("تعداد")

    class Meta:
        verbose_name = "قلم سفارش"
        verbose_name_plural = "اقلام سفارش"

    def __str__(self):
        return f"{self.product_name} × {self.quantity}"

    @property
    def line_total(self):
        return self.unit_price * self.quantity


class PharmacyQuestion(models.Model):
    """درخواست مشاوره دارویی از داروساز."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="pharmacy_questions",
        verbose_name="کاربر",
    )
    product = models.ForeignKey(
        Product, on_delete=models.SET_NULL, null=True, blank=True, related_name="questions", verbose_name="محصول"
    )
    name = models.CharField("نام", max_length=120)
    mobile = models.CharField("موبایل", max_length=11, validators=[validate_mobile])
    question = models.TextField("سؤال", max_length=2000)
    answer = models.TextField("پاسخ داروساز", blank=True)
    answered_at = models.DateTimeField("زمان پاسخ", null=True, blank=True)
    created_at = models.DateTimeField("زمان ثبت", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "مشاوره دارویی"
        verbose_name_plural = "مشاوره‌های دارویی"

    def __str__(self):
        return f"{self.name}: {self.question[:40]}"

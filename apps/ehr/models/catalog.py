"""کاتالوگ‌های رسمی: کدینگ خدمات سلامت (کتاب ارزش نسبی)، ضریب ریالی سالانه و فهرست داروهای ژنریک."""

from decimal import ROUND_HALF_UP, Decimal

from django.db import models


class ServiceCode(models.Model):
    """یک کد ملی خدمت از «اکسل ابلاغی کدینگ خدمات سلامت»؛ ارزش‌ها واحد ارزش نسبی (RVU) هستند، نه ریال."""

    code = models.CharField("کد ملی", max_length=12, unique=True)
    attribute = models.CharField("ویژگی کد", max_length=10, blank=True, help_text="مثلاً * (زیبایی / غیر بیمه‌ای) یا #")
    title = models.TextField("شرح کد")
    description = models.TextField("توضیحات", blank=True)
    sheet = models.CharField("بخش", max_length=60, blank=True, db_index=True)
    total_rvu = models.DecimalField("ارزش نسبی کل", max_digits=10, decimal_places=2, null=True, blank=True)
    professional_rvu = models.DecimalField("ارزش نسبی جزء حرفه‌ای", max_digits=10, decimal_places=2, null=True, blank=True)
    technical_rvu = models.DecimalField("ارزش نسبی جزء فنی", max_digits=10, decimal_places=2, null=True, blank=True)
    anesthesia_base = models.DecimalField("ارزش پایه بیهوشی", max_digits=6, decimal_places=2, null=True, blank=True)
    edition = models.CharField("نسخه کتاب", max_length=10, default="1404")
    is_active = models.BooleanField("فعال", default=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "کد خدمت سلامت"
        verbose_name_plural = "کدینگ خدمات سلامت (ارزش نسبی)"

    def __str__(self):
        return f"{self.code} {self.title[:80]}"

    @property
    def is_cosmetic(self):
        return "*" in self.attribute

    def tariff(self, sector, year=None):
        """تعرفه ریالی = جزء حرفه‌ای × ضریب حرفه‌ای + جزء فنی × ضریب فنی (یا کل × ضریب کل). None یعنی ضریب ثبت نشده."""
        coefficients = RVUCoefficient.for_year(sector, year)
        if not coefficients:
            return None
        if self.professional_rvu is not None and RVUCoefficient.Component.PROFESSIONAL in coefficients:
            total = self.professional_rvu * coefficients[RVUCoefficient.Component.PROFESSIONAL]
            if self.technical_rvu:
                tech = coefficients.get(RVUCoefficient.Component.TECHNICAL, coefficients[RVUCoefficient.Component.PROFESSIONAL])
                total += self.technical_rvu * tech
        elif self.total_rvu is not None and RVUCoefficient.Component.TOTAL in coefficients:
            total = self.total_rvu * coefficients[RVUCoefficient.Component.TOTAL]
        else:
            return None
        return int(Decimal(total).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


class RVUCoefficient(models.Model):
    """ضریب ریالی هر واحد ارزش نسبی که هیئت وزیران هر سال ابلاغ می‌کند؛ مدیر وارد می‌کند."""

    class Sector(models.TextChoices):
        PUBLIC = "public", "دولتی"
        NONPROFIT = "nonprofit", "عمومی غیردولتی / خیریه"
        PRIVATE = "private", "خصوصی"

    class Component(models.TextChoices):
        PROFESSIONAL = "professional", "جزء حرفه‌ای"
        TECHNICAL = "technical", "جزء فنی"
        TOTAL = "total", "کل (اگر ضریب حرفه‌ای و فنی یکی است)"

    year = models.PositiveSmallIntegerField("سال شمسی")
    sector = models.CharField("بخش", max_length=10, choices=Sector.choices)
    component = models.CharField("جزء", max_length=12, choices=Component.choices)
    value = models.DecimalField("ضریب ریالی هر واحد (ریال)", max_digits=14, decimal_places=2)
    reference = models.CharField("مصوبه / شماره ابلاغ", max_length=150, blank=True)

    class Meta:
        ordering = ["-year", "sector", "component"]
        verbose_name = "ضریب ریالی ارزش نسبی"
        verbose_name_plural = "ضرایب ریالی ارزش نسبی"
        constraints = [models.UniqueConstraint(fields=["year", "sector", "component"], name="unique_rvu_coefficient")]

    def __str__(self):
        return f"{self.year} {self.get_sector_display()} {self.get_component_display()}: {self.value}"

    @classmethod
    def for_year(cls, sector, year=None):
        rows = cls.objects.filter(sector=sector)
        if year:
            rows = rows.filter(year=year)
        latest = rows.order_by("-year").values_list("year", flat=True).first()
        if latest is None:
            return {}
        return {c.component: c.value for c in rows.filter(year=latest)}


class GenericDrug(models.Model):
    """داروی ژنریک (فهرست رسمی داروهای ایران). کالاهای داروخانه به آن وصل می‌شوند تا پزشک موجودی را ببیند."""

    class Category(models.TextChoices):
        OTC = "otc", "بدون نسخه (OTC)"
        RX = "rx", "نسخه‌ای"
        CONTROLLED = "controlled", "تحت کنترل (مخدر / روان‌گردان)"

    code = models.CharField("کد ژنریک", max_length=20, unique=True, null=True, blank=True)
    name = models.CharField("نام ژنریک (لاتین)", max_length=200)
    name_fa = models.CharField("نام فارسی", max_length=200, blank=True)
    dosage_form = models.CharField("شکل دارویی", max_length=100)
    strength = models.CharField("قدرت / مقدار", max_length=100, blank=True)
    route = models.CharField("راه مصرف", max_length=60, blank=True)
    atc_code = models.CharField("کد ATC", max_length=10, blank=True)
    category = models.CharField("دسته", max_length=10, choices=Category.choices, default=Category.RX)
    default_sig = models.CharField("دستور مصرف پیش‌فرض", max_length=200, blank=True)
    needs_verification = models.BooleanField(
        "نیازمند تطبیق با فهرست رسمی", default=False, help_text="ردیف‌های نمونه تا تطبیق با فهرست سازمان غذا و دارو علامت دارند."
    )
    is_active = models.BooleanField("فعال", default=True)

    class Meta:
        ordering = ["name", "dosage_form", "strength"]
        verbose_name = "داروی ژنریک"
        verbose_name_plural = "فهرست داروهای ژنریک"
        constraints = [
            models.UniqueConstraint(fields=["name", "dosage_form", "strength"], name="unique_generic_form_strength")
        ]

    def __str__(self):
        return " ".join(part for part in (self.name, self.dosage_form, self.strength) if part)

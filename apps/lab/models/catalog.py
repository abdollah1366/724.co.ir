"""کاتالوگ آزمایش‌ها: بخش‌ها، نوع نمونه، آمادگی، آزمایش، محدوده مرجع، پنل و راهنمای آزمایش‌های دوره‌ای."""

from decimal import Decimal

from django.db import models
from django.urls import reverse


class Sex(models.TextChoices):
    ANY = "any", "هر دو"
    MALE = "m", "مرد"
    FEMALE = "f", "زن"


class LabSection(models.Model):
    """بخش فنی آزمایشگاه (هماتولوژی، بیوشیمی، هورمون، میکروب‌شناسی، ...)."""

    code = models.CharField("کد", max_length=10, unique=True)
    name = models.CharField("نام بخش", max_length=80)
    order = models.PositiveSmallIntegerField("ترتیب", default=0)
    is_active = models.BooleanField("فعال", default=True)

    class Meta:
        ordering = ["order", "name"]
        verbose_name = "بخش فنی"
        verbose_name_plural = "بخش‌های فنی آزمایشگاه"

    def __str__(self):
        return self.name


class SpecimenType(models.Model):
    code = models.CharField("کد", max_length=10, unique=True)
    name = models.CharField("نوع نمونه", max_length=80)
    container = models.CharField("لوله / ظرف", max_length=80, blank=True, help_text="مثلاً لوله درب بنفش EDTA")
    color = models.CharField("رنگ برچسب", max_length=20, blank=True, help_text="کد رنگ CSS مثل #7c3aed")
    collection_note = models.CharField("نکته نمونه‌گیری", max_length=200, blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "نوع نمونه"
        verbose_name_plural = "انواع نمونه"

    def __str__(self):
        return self.name


class PrepInstruction(models.Model):
    """متن آمادگی قابل استفاده مجدد (ناشتایی، قطع دارو با نظر پزشک، نمونه صبحگاهی، ...)."""

    title = models.CharField("عنوان", max_length=120, unique=True)
    text = models.TextField("شرح آمادگی")
    fasting_hours = models.PositiveSmallIntegerField("ساعت ناشتایی", null=True, blank=True)
    order = models.PositiveSmallIntegerField("ترتیب", default=0)

    class Meta:
        ordering = ["order", "title"]
        verbose_name = "شرایط آمادگی"
        verbose_name_plural = "شرایط آمادگی"

    def __str__(self):
        return self.title


class LabTestQuerySet(models.QuerySet):
    def public(self):
        return self.filter(is_active=True, is_public=True)


class LabTest(models.Model):
    class ResultType(models.TextChoices):
        NUMERIC = "numeric", "عددی"
        TEXT = "text", "متنی"
        CHOICE = "choice", "انتخابی"

    code = models.CharField("کد آزمایش", max_length=20, unique=True, help_text="کد داخلی و کد دستگاه، مثل FBS")
    name = models.CharField("نام فارسی", max_length=150)
    name_en = models.CharField("نام لاتین", max_length=150, blank=True)
    slug = models.SlugField("نامک", unique=True, allow_unicode=True)
    aliases = models.CharField("نام‌های دیگر", max_length=300, blank=True, help_text="برای جست‌وجو؛ با ویرگول جدا کنید")
    section = models.ForeignKey(LabSection, on_delete=models.PROTECT, related_name="tests", verbose_name="بخش فنی")
    specimen_type = models.ForeignKey(SpecimenType, on_delete=models.PROTECT, related_name="tests", verbose_name="نوع نمونه")
    preparations = models.ManyToManyField(PrepInstruction, blank=True, related_name="tests", verbose_name="شرایط آمادگی")
    description = models.TextField("این آزمایش چه چیزی را می‌سنجد؟", blank=True)
    indications = models.TextField("چه زمانی درخواست می‌شود؟", blank=True)
    result_type = models.CharField("نوع جواب", max_length=8, choices=ResultType.choices, default=ResultType.NUMERIC)
    unit = models.CharField("واحد", max_length=30, blank=True)
    decimals = models.PositiveSmallIntegerField("رقم اعشار", default=0)
    choices = models.CharField("گزینه‌های جواب", max_length=300, blank=True, help_text="برای جواب انتخابی؛ با ویرگول جدا کنید (مثل منفی، مثبت)")
    normal_text = models.CharField("جواب طبیعی (متنی)", max_length=100, blank=True, help_text="مثلاً «منفی»؛ جواب دیگر غیرطبیعی علامت می‌خورد")
    delta_percent = models.PositiveSmallIntegerField(
        "حد هشدار تغییر نسبت به جواب قبلی (٪)", null=True, blank=True, help_text="Delta check؛ خالی = بدون بررسی"
    )
    tariff_code = models.CharField("کد ملی تعرفه", max_length=20, blank=True)
    price = models.PositiveBigIntegerField("تعرفه آزاد (ریال)", default=0)
    turnaround_hours = models.PositiveSmallIntegerField("زمان جواب‌دهی (ساعت)", default=24)
    recall_months = models.PositiveSmallIntegerField(
        "یادآوری تکرار (ماه)", null=True, blank=True, help_text="برای بیماران با رضایت یادآوری؛ مثلاً HbA1c هر ۳ ماه"
    )
    home_sampling = models.BooleanField("قابل نمونه‌گیری در منزل", default=True)
    is_sensitive = models.BooleanField(
        "آزمایش حساس", default=False,
        help_text="جواب آنلاین نمایش داده نمی‌شود و فقط حضوری با مشاوره تحویل می‌شود (مثل HIV یا ژنتیک)",
    )
    is_public = models.BooleanField("نمایش در سایت", default=True)
    is_active = models.BooleanField("فعال", default=True)
    order = models.PositiveSmallIntegerField("ترتیب در برگه جواب", default=0)

    objects = LabTestQuerySet.as_manager()

    class Meta:
        ordering = ["section__order", "order", "name"]
        verbose_name = "آزمایش"
        verbose_name_plural = "آزمایش‌ها"

    def __str__(self):
        return f"{self.name} ({self.code})"

    def get_absolute_url(self):
        return reverse("lab:test_detail", args=[self.slug])

    @property
    def choice_list(self):
        return [c.strip() for c in self.choices.replace("،", ",").split(",") if c.strip()]

    @property
    def fasting_hours(self):
        hours = [p.fasting_hours for p in self.preparations.all() if p.fasting_hours]
        return max(hours) if hours else None

    def reference_range_for(self, sex, age_days):
        """محدوده مرجع مناسب بیمار: اختصاصی جنس بر «هر دو» مقدم است."""
        candidates = []
        for rr in self.reference_ranges.all():
            if rr.sex not in (Sex.ANY, sex):
                continue
            if age_days is not None:
                if rr.age_min_days is not None and age_days < rr.age_min_days:
                    continue
                if rr.age_max_days is not None and age_days > rr.age_max_days:
                    continue
            candidates.append(rr)
        candidates.sort(key=lambda r: (r.sex == Sex.ANY, r.age_min_days is None and r.age_max_days is None))
        return candidates[0] if candidates else None


class ReferenceRange(models.Model):
    test = models.ForeignKey(LabTest, on_delete=models.CASCADE, related_name="reference_ranges", verbose_name="آزمایش")
    sex = models.CharField("جنس", max_length=3, choices=Sex.choices, default=Sex.ANY)
    age_min_days = models.PositiveIntegerField("حداقل سن (روز)", null=True, blank=True)
    age_max_days = models.PositiveIntegerField("حداکثر سن (روز)", null=True, blank=True)
    low = models.DecimalField("حد پایین", max_digits=12, decimal_places=4, null=True, blank=True)
    high = models.DecimalField("حد بالا", max_digits=12, decimal_places=4, null=True, blank=True)
    critical_low = models.DecimalField("بحرانی پایین", max_digits=12, decimal_places=4, null=True, blank=True)
    critical_high = models.DecimalField("بحرانی بالا", max_digits=12, decimal_places=4, null=True, blank=True)
    text = models.CharField("متن محدوده", max_length=120, blank=True, help_text="اگر خالی باشد از حد پایین و بالا ساخته می‌شود")

    class Meta:
        verbose_name = "محدوده مرجع"
        verbose_name_plural = "محدوده‌های مرجع"

    def __str__(self):
        return self.display

    @property
    def display(self):
        if self.text:
            return self.text
        fmt = lambda v: format(v.normalize(), "f") if isinstance(v, Decimal) else str(v)  # noqa: E731
        if self.low is not None and self.high is not None:
            return f"{fmt(self.low)} - {fmt(self.high)}"
        if self.high is not None:
            return f"< {fmt(self.high)}"
        if self.low is not None:
            return f"> {fmt(self.low)}"
        return ""


class LabPanel(models.Model):
    """گروه آزمایش (مثل چکاپ عمومی) با قیمت بسته اختیاری."""

    code = models.CharField("کد", max_length=20, unique=True)
    name = models.CharField("نام", max_length=150)
    slug = models.SlugField("نامک", unique=True, allow_unicode=True)
    description = models.TextField("توضیحات", blank=True)
    tests = models.ManyToManyField(LabTest, related_name="panels", verbose_name="آزمایش‌ها")
    package_price = models.PositiveBigIntegerField(
        "قیمت بسته آزاد (ریال)", null=True, blank=True, help_text="خالی = جمع تعرفه آزمایش‌ها"
    )
    is_public = models.BooleanField("نمایش در سایت", default=True)
    is_active = models.BooleanField("فعال", default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "پنل / بسته آزمایش"
        verbose_name_plural = "پنل‌ها و بسته‌های آزمایش"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("lab:panel_detail", args=[self.slug])

    @property
    def list_price(self):
        return sum(t.price for t in self.tests.all())

    @property
    def price(self):
        return self.package_price if self.package_price is not None else self.list_price


class RiskFactor(models.TextChoices):
    NONE = "", "همه افراد"
    DIABETES = "diabetes", "دیابت"
    HYPERTENSION = "hypertension", "فشارخون بالا"
    OBESITY = "obesity", "اضافه‌وزن یا چاقی"
    SMOKING = "smoking", "مصرف دخانیات"
    FAMILY_CVD = "family_cvd", "سابقه خانوادگی بیماری قلبی"
    FAMILY_DIABETES = "family_diabetes", "سابقه خانوادگی دیابت"
    KIDNEY = "kidney", "بیماری کلیه"
    PREGNANCY = "pregnancy", "بارداری یا برنامه بارداری"


class ScreeningRule(models.Model):
    """راهنمای آزمایش دوره‌ای بر اساس سن، جنس و عامل خطر. فقط قواعد تأییدشده مسئول فنی در سایت دیده می‌شوند."""

    title = models.CharField("عنوان", max_length=150)
    sex = models.CharField("جنس", max_length=3, choices=Sex.choices, default=Sex.ANY)
    age_min = models.PositiveSmallIntegerField("از سن (سال)", default=0)
    age_max = models.PositiveSmallIntegerField("تا سن (سال)", default=120)
    risk_factor = models.CharField("عامل خطر", max_length=20, choices=RiskFactor.choices, blank=True)
    interval = models.CharField("تناوب پیشنهادی", max_length=100, help_text="مثلاً «هر ۳ سال»")
    tests = models.ManyToManyField(LabTest, blank=True, related_name="screening_rules", verbose_name="آزمایش‌ها")
    rationale = models.TextField("چرا مهم است؟")
    source = models.CharField("منبع علمی", max_length=300, blank=True)
    is_approved = models.BooleanField("تأیید مسئول فنی", default=False)
    order = models.PositiveSmallIntegerField("ترتیب", default=0)

    class Meta:
        ordering = ["order", "age_min"]
        verbose_name = "راهنمای آزمایش دوره‌ای"
        verbose_name_plural = "راهنمای آزمایش‌های دوره‌ای"

    def __str__(self):
        return self.title

    def applies_to(self, age, sex, risks):
        if self.sex != Sex.ANY and self.sex != sex:
            return False
        if not (self.age_min <= age <= self.age_max):
            return False
        return not self.risk_factor or self.risk_factor in risks

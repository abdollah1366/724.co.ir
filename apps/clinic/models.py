import datetime

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone

# روزهای هفته با شماره‌گذاری پایتون (دوشنبه=۰) و ترتیب نمایش ایرانی (از شنبه).
WEEKDAY_CHOICES = [
    (5, "شنبه"),
    (6, "یکشنبه"),
    (0, "دوشنبه"),
    (1, "سه‌شنبه"),
    (2, "چهارشنبه"),
    (3, "پنج‌شنبه"),
    (4, "جمعه"),
]


class Floor(models.Model):
    """یک طبقه از ساختمان ۷۲۴ (داروخانه، پزشک عمومی، متخصصین، ...)."""

    number = models.SmallIntegerField("شماره طبقه", unique=True, help_text="همکف = ۰")
    title = models.CharField("عنوان", max_length=120)
    slug = models.SlugField("نامک", unique=True, allow_unicode=True)
    summary = models.CharField("خلاصه", max_length=300)
    description = models.TextField("توضیحات", blank=True)
    icon = models.CharField("آیکون (ایموجی)", max_length=8, blank=True)
    opening_hours = models.CharField("ساعات کاری", max_length=200, blank=True)
    is_active = models.BooleanField("فعال", default=True)
    is_coming_soon = models.BooleanField(
        "به‌زودی", default=False, help_text="برای خدماتی که هنوز راه‌اندازی نشده‌اند"
    )

    class Meta:
        ordering = ["number"]
        verbose_name = "طبقه"
        verbose_name_plural = "طبقات"

    def __str__(self):
        return self.title

    @property
    def label(self):
        return "همکف" if self.number == 0 else f"طبقه {self.number}"

    def get_absolute_url(self):
        return reverse("clinic:floor_detail", args=[self.slug])


class Service(models.Model):
    floor = models.ForeignKey(Floor, on_delete=models.CASCADE, related_name="services", verbose_name="طبقه")
    title = models.CharField("عنوان خدمت", max_length=150)
    description = models.TextField("توضیحات", blank=True)
    price = models.PositiveBigIntegerField("تعرفه (ریال)", null=True, blank=True)
    price_note = models.CharField("توضیح تعرفه", max_length=200, blank=True)
    requires_qualified_provider = models.BooleanField(
        "نیازمند ارائه‌دهنده دارای صلاحیت",
        default=False,
        help_text="مثلاً خدمات زیبایی تزریقی؛ فقط توسط پزشک دارای مجوز ارائه شود.",
    )
    order = models.PositiveSmallIntegerField("ترتیب", default=0)
    is_active = models.BooleanField("فعال", default=True)

    class Meta:
        ordering = ["floor__number", "order", "title"]
        verbose_name = "خدمت"
        verbose_name_plural = "خدمات"

    def __str__(self):
        return self.title


class Specialty(models.Model):
    name = models.CharField("نام تخصص", max_length=120, unique=True)
    slug = models.SlugField("نامک", unique=True, allow_unicode=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "تخصص"
        verbose_name_plural = "تخصص‌ها"

    def __str__(self):
        return self.name


class DoctorQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)


class Doctor(models.Model):
    first_name = models.CharField("نام", max_length=80)
    last_name = models.CharField("نام خانوادگی", max_length=80)
    slug = models.SlugField("نامک", unique=True, allow_unicode=True)
    title = models.CharField("عنوان", max_length=120, default="دکتر")
    specialty = models.ForeignKey(
        Specialty, on_delete=models.PROTECT, null=True, blank=True, related_name="doctors", verbose_name="تخصص"
    )
    floor = models.ForeignKey(Floor, on_delete=models.PROTECT, related_name="doctors", verbose_name="طبقه")
    medical_council_number = models.CharField(
        "شماره نظام پزشکی", max_length=20, unique=True, help_text="برای نمایش عمومی و احراز صلاحیت الزامی است."
    )
    photo = models.ImageField("تصویر", upload_to="doctors/", blank=True)
    bio = models.TextField("رزومه و معرفی", blank=True)
    visit_fee = models.PositiveBigIntegerField("هزینه ویزیت (ریال)", null=True, blank=True)
    accepts_online_booking = models.BooleanField("نوبت‌دهی اینترنتی", default=True)
    is_active = models.BooleanField("فعال", default=True)

    objects = DoctorQuerySet.as_manager()

    class Meta:
        ordering = ["floor__number", "last_name"]
        verbose_name = "پزشک"
        verbose_name_plural = "پزشکان"

    def __str__(self):
        return self.full_name

    @property
    def full_name(self):
        return f"{self.title} {self.first_name} {self.last_name}".strip()

    def get_absolute_url(self):
        return reverse("clinic:doctor_detail", args=[self.slug])

    def available_dates(self, days=None):
        """روزهای آینده‌ای که پزشک برنامه کاری دارد."""
        days = days or settings.APPOINTMENT_BOOKING_DAYS_AHEAD
        weekdays = set(self.working_hours.values_list("weekday", flat=True))
        today = timezone.localdate()
        return [
            today + datetime.timedelta(days=i)
            for i in range(days + 1)
            if (today + datetime.timedelta(days=i)).weekday() in weekdays
        ]

    def available_slots(self, date):
        """زمان‌های آزاد پزشک در یک روز مشخص (بدون نوبت‌های رزرو شده و زمان‌های گذشته)."""
        if not self.accepts_online_booking or not self.is_active:
            return []
        today = timezone.localdate()
        if date < today or date > today + datetime.timedelta(days=settings.APPOINTMENT_BOOKING_DAYS_AHEAD):
            return []

        slots = []
        for wh in self.working_hours.filter(weekday=date.weekday()):
            slots.extend(wh.slot_times())

        taken = set(self.appointments.active().filter(date=date).values_list("time", flat=True))
        now = timezone.localtime().time() if date == today else None
        return sorted(t for t in set(slots) if t not in taken and (now is None or t > now))


class WorkingHours(models.Model):
    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name="working_hours", verbose_name="پزشک")
    weekday = models.PositiveSmallIntegerField("روز هفته", choices=WEEKDAY_CHOICES)
    start_time = models.TimeField("از ساعت")
    end_time = models.TimeField("تا ساعت")
    slot_minutes = models.PositiveSmallIntegerField("مدت هر نوبت (دقیقه)", default=15)

    class Meta:
        ordering = ["doctor", "weekday", "start_time"]
        verbose_name = "ساعت حضور"
        verbose_name_plural = "برنامه حضور"
        constraints = [
            models.CheckConstraint(condition=models.Q(end_time__gt=models.F("start_time")), name="working_hours_end_after_start"),
            models.CheckConstraint(condition=models.Q(slot_minutes__gte=5), name="working_hours_slot_min_5"),
        ]

    def __str__(self):
        return f"{self.get_weekday_display()} {self.start_time:%H:%M}-{self.end_time:%H:%M}"

    def slot_times(self):
        base = datetime.date(2000, 1, 1)
        current = datetime.datetime.combine(base, self.start_time)
        end = datetime.datetime.combine(base, self.end_time)
        step = datetime.timedelta(minutes=self.slot_minutes)
        times = []
        while current + step <= end:
            times.append(current.time())
            current += step
        return times

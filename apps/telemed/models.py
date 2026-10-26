"""مشاوره و ویزیت آنلاین: پرسش رایگان و گفتگوی پولی با پزشک، تماس تلفنی/تصویری در ساعت آنلاین، و مشاوره دارویی رایگان.

محرمانگی: گفتگو را فقط بیمار، پزشک همان مشاوره و داروسازان (برای مشاوره دارویی) می‌بینند و هر بار باز شدنش ثبت می‌شود.
متن گفتگو و موضوع در پنل مدیریت و گزارش تغییرات نمی‌آید؛ مدیر کل فقط وضعیت، مبلغ و پرداخت را می‌بیند.
"""

import datetime
import secrets
import uuid
from pathlib import Path

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone

from apps.clinic.models import WEEKDAY_CHOICES, Doctor
from apps.core.fields import unique_when
from apps.ehr.models.documents import ALLOWED_EXTENSIONS, get_private_storage, validate_medical_file


def new_authority():
    return uuid.uuid4().hex


def generate_code():
    return "".join(secrets.choice("0123456789") for _ in range(10))


class DoctorOnlineProfile(models.Model):
    """کدام نوع مشاوره آنلاین را هر پزشک ارائه می‌دهد و با چه تعرفه‌ای."""

    doctor = models.OneToOneField(Doctor, on_delete=models.CASCADE, related_name="online", verbose_name="پزشک")
    is_enabled = models.BooleanField("مشاوره آنلاین فعال", default=False)
    free_enabled = models.BooleanField("پرسش متنی رایگان", default=False)
    free_daily_limit = models.PositiveSmallIntegerField(
        "سقف پرسش رایگان در روز", default=5, help_text="پس از رسیدن به سقف، تا فردا پرسش رایگان جدید پذیرفته نمی‌شود."
    )
    text_fee = models.PositiveBigIntegerField(
        "تعرفه گفتگوی متنی (ریال)", null=True, blank=True, help_text="خالی = گفتگوی متنی پولی ارائه نمی‌شود."
    )
    call_fee = models.PositiveBigIntegerField("تعرفه ویزیت تلفنی (ریال)", null=True, blank=True)
    video_fee = models.PositiveBigIntegerField("تعرفه ویزیت تصویری (ریال)", null=True, blank=True)
    slot_minutes = models.PositiveSmallIntegerField("مدت هر ویزیت تلفنی/تصویری (دقیقه)", default=15)
    response_hours = models.PositiveSmallIntegerField(
        "مهلت پاسخ (ساعت)", default=24,
        help_text="اگر تا این مدت پاسخ داده نشود، مشاوره منقضی و مبلغ پرداختی قابل استرداد می‌شود.",
    )
    notes = models.CharField("توضیح برای بیماران", max_length=300, blank=True)

    class Meta:
        verbose_name = "تنظیمات مشاوره آنلاین پزشک"
        verbose_name_plural = "تنظیمات مشاوره آنلاین پزشکان"
        constraints = [models.CheckConstraint(condition=models.Q(slot_minutes__gte=5), name="online_slot_min_5")]

    def __str__(self):
        return str(self.doctor)

    def fee_for(self, kind):
        return {
            Consultation.Kind.FREE: 0 if self.free_enabled else None,
            Consultation.Kind.TEXT: self.text_fee,
            Consultation.Kind.CALL: self.call_fee,
            Consultation.Kind.VIDEO: self.video_fee,
        }.get(kind)

    def offered_kinds(self):
        return [kind for kind in Consultation.Kind if self.fee_for(kind) is not None]

    def free_left_today(self):
        start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
        used = self.doctor.consultations.filter(kind=Consultation.Kind.FREE, created_at__gte=start).exclude(
            status=Consultation.Status.CANCELLED
        ).count()
        return max(self.free_daily_limit - used, 0)

    def available_slots(self, date):
        """زمان‌های آزاد ویزیت تلفنی/تصویری در یک روز (بدون زمان‌های رزروشده، گذشته یا کمتر از یک ساعت بعد)."""
        today = timezone.localdate()
        if date < today or date > today + datetime.timedelta(days=settings.TELEMED_BOOKING_DAYS_AHEAD):
            return []
        step = datetime.timedelta(minutes=self.slot_minutes)
        times = set()
        for hours in self.doctor.online_hours.filter(weekday=date.weekday()):
            current = datetime.datetime.combine(date, hours.start_time)
            end = datetime.datetime.combine(date, hours.end_time)
            while current + step <= end:
                times.add(current.time())
                current += step
        tz = timezone.get_current_timezone()
        earliest = timezone.now() + datetime.timedelta(minutes=settings.TELEMED_MIN_NOTICE_MINUTES)
        taken = {
            timezone.localtime(at).time()
            for at in self.doctor.consultations.holding_slot().filter(scheduled_at__date=date).values_list(
                "scheduled_at", flat=True
            )
        }
        # زمان نوبت‌های حضوری همان روز هم آزاد نیست
        taken |= set(self.doctor.appointments.active().filter(date=date).values_list("time", flat=True))
        return sorted(
            t for t in times
            if t not in taken and datetime.datetime.combine(date, t, tzinfo=tz) >= earliest
        )

    def available_dates(self):
        weekdays = set(self.doctor.online_hours.values_list("weekday", flat=True))
        today = timezone.localdate()
        days = (today + datetime.timedelta(days=i) for i in range(settings.TELEMED_BOOKING_DAYS_AHEAD + 1))
        return [day for day in days if day.weekday() in weekdays]


class OnlineHours(models.Model):
    """ساعت‌هایی که پزشک برای ویزیت تلفنی/تصویری در دسترس است (جدا از برنامه حضور در مطب)."""

    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name="online_hours", verbose_name="پزشک")
    weekday = models.PositiveSmallIntegerField("روز هفته", choices=WEEKDAY_CHOICES)
    start_time = models.TimeField("از ساعت")
    end_time = models.TimeField("تا ساعت")

    class Meta:
        ordering = ["doctor", "weekday", "start_time"]
        verbose_name = "ساعت مشاوره آنلاین"
        verbose_name_plural = "ساعت‌های مشاوره آنلاین"
        constraints = [
            models.CheckConstraint(condition=models.Q(end_time__gt=models.F("start_time")), name="online_hours_end_after_start"),
        ]

    def __str__(self):
        return f"{self.get_weekday_display()} {self.start_time:%H:%M}-{self.end_time:%H:%M}"


class ConsultationQuerySet(models.QuerySet):
    def holding_slot(self):
        """مشاوره‌هایی که زمان ویزیت را نگه داشته‌اند (در انتظار پرداخت یا فعال)."""
        return self.filter(scheduled_at__isnull=False, status__in=Consultation.SLOT_STATUSES)

    def open(self):
        return self.filter(status__in=Consultation.OPEN_STATUSES)

    def visible_to_provider(self, user):
        """مشاوره‌هایی که این کارمند به‌عنوان پاسخ‌دهنده می‌بیند."""
        q = models.Q(pk__in=[])
        if user.has_perm("telemed.answer_consultation") and getattr(user, "doctor", None) is not None:
            q |= models.Q(channel=Consultation.Channel.DOCTOR, doctor=user.doctor)
        if user.has_perm("telemed.answer_pharmacy"):
            q |= models.Q(channel=Consultation.Channel.PHARMACY)
        return self.filter(q).exclude(status=Consultation.Status.AWAITING_PAYMENT)


class Consultation(models.Model):
    class Channel(models.TextChoices):
        DOCTOR = "doctor", "پزشک"
        PHARMACY = "pharmacy", "داروساز"

    class Kind(models.TextChoices):
        FREE = "free", "پرسش متنی رایگان"
        TEXT = "text", "گفتگوی متنی"
        CALL = "call", "ویزیت تلفنی"
        VIDEO = "video", "ویزیت تصویری"

    class Status(models.TextChoices):
        AWAITING_PAYMENT = "unpaid", "در انتظار پرداخت"
        WAITING = "waiting", "در انتظار پاسخ"
        ANSWERED = "answered", "پاسخ داده شد"
        CLOSED = "closed", "پایان یافته"
        DECLINED = "declined", "رد شده"
        CANCELLED = "cancelled", "لغو شده"
        EXPIRED = "expired", "منقضی شده"

    class ForWhom(models.TextChoices):
        SELF = "self", "خودم"
        CHILD = "child", "فرزندم"
        OTHER = "other", "یکی از اعضای خانواده"

    class Sex(models.TextChoices):
        FEMALE = "f", "زن"
        MALE = "m", "مرد"

    class Outcome(models.TextChoices):
        ADVICE = "advice", "توصیه داده شد"
        VISIT = "visit", "نیاز به ویزیت حضوری"
        LAB = "lab", "نیاز به آزمایش"
        EMERGENCY = "emergency", "ارجاع فوری به اورژانس"
        OTHER = "other", "سایر"

    OPEN_STATUSES = (Status.WAITING, Status.ANSWERED)
    SLOT_STATUSES = (Status.AWAITING_PAYMENT, Status.WAITING, Status.ANSWERED, Status.CLOSED)
    LIVE_KINDS = (Kind.CALL, Kind.VIDEO)

    code = models.CharField("کد مشاوره", max_length=10, unique=True, default=generate_code, editable=False)
    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="consultations", verbose_name="بیمار (حساب)"
    )
    channel = models.CharField("با", max_length=8, choices=Channel.choices, default=Channel.DOCTOR)
    doctor = models.ForeignKey(
        Doctor, on_delete=models.PROTECT, null=True, blank=True, related_name="consultations", verbose_name="پزشک"
    )
    kind = models.CharField("نوع", max_length=6, choices=Kind.choices)
    status = models.CharField("وضعیت", max_length=9, choices=Status.choices, default=Status.WAITING, db_index=True)
    fee = models.PositiveBigIntegerField("مبلغ (ریال)", default=0)

    subject = models.CharField("موضوع", max_length=150)
    for_whom = models.CharField("برای", max_length=5, choices=ForWhom.choices, default=ForWhom.SELF)
    age = models.PositiveSmallIntegerField("سن بیمار (سال)")
    sex = models.CharField("جنسیت بیمار", max_length=1, choices=Sex.choices)
    scheduled_at = models.DateTimeField("زمان ویزیت", null=True, blank=True)
    meeting_link = models.URLField(
        "پیوند تماس تصویری", blank=True, help_text="پیوند جلسه در سرویس تماس تصویری مورد تأیید مجموعه"
    )
    sms_opt_in = models.BooleanField("اطلاع‌رسانی پیامکی", default=True)
    consent_at = models.DateTimeField("زمان رضایت")

    taken_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+",
        verbose_name="پاسخ‌دهنده",
    )
    outcome = models.CharField("نتیجه", max_length=9, choices=Outcome.choices, blank=True)
    decline_reason = models.CharField("علت رد / لغو", max_length=300, blank=True)
    created_at = models.DateTimeField("زمان ثبت", auto_now_add=True, db_index=True)
    submitted_at = models.DateTimeField("زمان ارسال برای پاسخ", null=True, blank=True)
    answered_at = models.DateTimeField("زمان اولین پاسخ", null=True, blank=True)
    closed_at = models.DateTimeField("زمان پایان", null=True, blank=True)
    reminded_at = models.DateTimeField("زمان یادآوری", null=True, blank=True, editable=False)
    slot_held = unique_when(models.Q(scheduled_at__isnull=False, status__in=SLOT_STATUSES))

    objects = ConsultationQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "مشاوره آنلاین"
        verbose_name_plural = "مشاوره‌های آنلاین"
        constraints = [
            models.UniqueConstraint(fields=["doctor", "scheduled_at", "slot_held"], name="consultation_unique_slot"),
            models.CheckConstraint(
                condition=models.Q(channel="pharmacy", doctor__isnull=True, kind="free")
                | models.Q(channel="doctor", doctor__isnull=False),
                name="consultation_channel_doctor",
            ),
        ]
        permissions = [
            ("answer_consultation", "پاسخ به مشاوره آنلاین بیماران خود (پزشک)"),
            ("answer_pharmacy", "پاسخ به مشاوره دارویی آنلاین (داروساز)"),
            ("manage_payments", "ثبت پرداخت دستی و استرداد مشاوره آنلاین"),
            ("view_telemed_report", "گزارش آماری مشاوره آنلاین"),
        ]

    def __str__(self):
        return f"{self.code} - {self.provider_name}"

    def get_absolute_url(self):
        return reverse("telemed:detail", args=[self.code])

    @property
    def provider_name(self):
        return self.doctor.full_name if self.doctor_id else "داروساز مسئول فنی"

    @property
    def is_live(self):
        return self.kind in self.LIVE_KINDS

    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES

    @property
    def is_paid_kind(self):
        return self.fee > 0

    def response_deadline(self):
        hours = self.doctor.online.response_hours if self.doctor_id and hasattr(self.doctor, "online") else settings.TELEMED_PHARMACY_RESPONSE_HOURS
        return (self.submitted_at or self.created_at) + datetime.timedelta(hours=hours)

    def paid_payment(self):
        return self.payments.filter(status__in=ConsultPayment.PAID_STATUSES).first()


class ConsultMessage(models.Model):
    class Role(models.TextChoices):
        PATIENT = "patient", "بیمار"
        PROVIDER = "provider", "پاسخ‌دهنده"
        SYSTEM = "system", "سامانه"

    consultation = models.ForeignKey(Consultation, on_delete=models.PROTECT, related_name="messages", verbose_name="مشاوره")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, related_name="+", verbose_name="فرستنده")
    role = models.CharField("نقش", max_length=8, choices=Role.choices)
    text = models.TextField("متن", max_length=4000, blank=True)
    created_at = models.DateTimeField("زمان", auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "پیام مشاوره"
        verbose_name_plural = "پیام‌های مشاوره"

    def delete(self, *args, **kwargs):
        raise PermissionError("پیام مشاوره پزشکی حذف نمی‌شود.")


def _upload_to(instance, filename):
    return f"telemed/{timezone.now():%Y/%m}/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"


class ConsultAttachment(models.Model):
    """عکس یا PDF (مثلاً برگه آزمایش) که در گفتگو فرستاده می‌شود؛ در پوشه خصوصی و فقط با مجوز باز می‌شود."""

    message = models.ForeignKey(ConsultMessage, on_delete=models.PROTECT, related_name="attachments", verbose_name="پیام")
    file = models.FileField("فایل", storage=get_private_storage, upload_to=_upload_to, validators=[validate_medical_file])
    content_type = models.CharField("نوع فایل", max_length=40, editable=False)
    size = models.PositiveIntegerField("حجم (بایت)", editable=False)

    class Meta:
        verbose_name = "پیوست مشاوره"
        verbose_name_plural = "پیوست‌های مشاوره"

    def save(self, *args, **kwargs):
        self.content_type = ALLOWED_EXTENSIONS.get(Path(self.file.name).suffix.lower(), "application/octet-stream")
        self.size = self.file.size
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("پیوست مشاوره پزشکی حذف نمی‌شود.")

    @property
    def is_image(self):
        return self.content_type.startswith("image/")


class ConsultPayment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "در انتظار"
        PAID = "paid", "پرداخت شد"
        FAILED = "failed", "ناموفق"
        REFUND_DUE = "refund_due", "قابل استرداد"
        REFUNDED = "refunded", "مسترد شد"

    class Method(models.TextChoices):
        ONLINE = "online", "درگاه اینترنتی"
        MANUAL = "manual", "ثبت دستی (کارت‌خوان / واریز)"

    PAID_STATUSES = (Status.PAID, Status.REFUND_DUE, Status.REFUNDED)

    consultation = models.ForeignKey(Consultation, on_delete=models.PROTECT, related_name="payments", verbose_name="مشاوره")
    amount = models.PositiveBigIntegerField("مبلغ (ریال)")
    method = models.CharField("روش", max_length=6, choices=Method.choices, default=Method.ONLINE)
    gateway = models.CharField("درگاه", max_length=20, blank=True)
    authority = models.CharField("شناسه درخواست", max_length=64, unique=True, default=new_authority, editable=False)
    reference = models.CharField("کد پیگیری بانکی", max_length=64, blank=True)
    status = models.CharField("وضعیت", max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    note = models.CharField("توضیح", max_length=300, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name="ثبت‌کننده",
    )
    created_at = models.DateTimeField("زمان ایجاد", auto_now_add=True)
    paid_at = models.DateTimeField("زمان پرداخت", null=True, blank=True)
    refunded_at = models.DateTimeField("زمان استرداد", null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "پرداخت مشاوره آنلاین"
        verbose_name_plural = "پرداخت‌های مشاوره آنلاین"

    def __str__(self):
        return f"{self.consultation.code} - {self.get_status_display()}"

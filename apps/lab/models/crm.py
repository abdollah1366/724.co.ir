"""مشاور و ارتباط با مشتری: درخواست مشاوره یا تماس، سابقه پیگیری، نظرسنجی رضایت و یادآوری آزمایش دوره‌ای."""

import datetime
import secrets

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from apps.audit.documents import ImmutableRecord
from apps.core.validators import validate_mobile

from .catalog import LabTest
from .orders import LabOrder, LabOrderItem
from .patients import LabPatient, random_code


def default_due():
    return timezone.now() + datetime.timedelta(hours=settings.LAB_RULES["consult_response_hours"])


class ConsultRequestQuerySet(models.QuerySet):
    def open(self):
        return self.exclude(status__in=[ConsultRequest.Status.RESOLVED, ConsultRequest.Status.CLOSED])

    def overdue(self):
        return self.open().filter(due_at__lt=timezone.now())


class ConsultRequest(models.Model):
    class Topic(models.TextChoices):
        TEST_INFO = "test_info", "راهنمای انتخاب آزمایش و آمادگی"
        PRICE = "price", "تعرفه، بیمه و هزینه"
        HOME = "home", "نمونه‌گیری در منزل"
        RESULT = "result", "پرسش درباره جواب (با مسئول فنی)"
        CHECKUP = "checkup", "برنامه چکاپ دوره‌ای"
        CONTRACT = "contract", "قرارداد سازمانی یا بیمه"
        COMPLAINT = "complaint", "انتقاد یا شکایت"
        OTHER = "other", "سایر"

    class Status(models.TextChoices):
        NEW = "new", "جدید"
        IN_PROGRESS = "in_progress", "در حال پیگیری"
        WAITING = "waiting", "منتظر پاسخ مشتری"
        RESOLVED = "resolved", "پاسخ داده شد"
        CLOSED = "closed", "بسته شد"

    class Channel(models.TextChoices):
        WEB = "web", "سایت"
        PHONE = "phone", "تلفن"
        VISIT = "visit", "حضوری"
        FEEDBACK = "feedback", "نظرسنجی"

    class Preferred(models.TextChoices):
        ANY = "any", "فرقی نمی‌کند"
        MORNING = "morning", "صبح"
        AFTERNOON = "afternoon", "عصر"

    topic = models.CharField("موضوع", max_length=10, choices=Topic.choices, default=Topic.TEST_INFO)
    full_name = models.CharField("نام", max_length=150)
    mobile = models.CharField("موبایل", max_length=11, validators=[validate_mobile])
    message = models.TextField(
        "شرح درخواست", max_length=1000, blank=True,
        help_text="لطفاً اطلاعات پزشکی حساس (مثل بیماری‌ها یا جواب آزمایش) را اینجا ننویسید؛ کارشناس با شما تماس می‌گیرد.",
    )
    preferred_time = models.CharField("زمان مناسب تماس", max_length=10, choices=Preferred.choices, default=Preferred.ANY)
    channel = models.CharField("کانال", max_length=8, choices=Channel.choices, default=Channel.WEB)
    status = models.CharField("وضعیت", max_length=12, choices=Status.choices, default=Status.NEW, db_index=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="lab_consults",
        verbose_name="کارشناس مسئول",
    )
    patient = models.ForeignKey(LabPatient, on_delete=models.SET_NULL, null=True, blank=True, related_name="consults", verbose_name="بیمار")
    order = models.ForeignKey(LabOrder, on_delete=models.SET_NULL, null=True, blank=True, related_name="consults", verbose_name="پذیرش مرتبط")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name="حساب کاربری"
    )
    tracking_code = models.CharField("کد پیگیری", max_length=8, unique=True, default=random_code, editable=False)
    consent_at = models.DateTimeField("رضایت به تماس", null=True, blank=True)
    created_at = models.DateTimeField("زمان ثبت", auto_now_add=True)
    due_at = models.DateTimeField("مهلت پاسخ", default=default_due)
    next_follow_up = models.DateTimeField("پیگیری بعدی", null=True, blank=True)
    resolved_at = models.DateTimeField("زمان پاسخ", null=True, blank=True)

    objects = ConsultRequestQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "درخواست مشاوره"
        verbose_name_plural = "درخواست‌های مشاوره و ارتباط با مشتری"

    def __str__(self):
        return f"{self.get_topic_display()} · {self.full_name}"

    @property
    def is_overdue(self):
        return self.status not in (self.Status.RESOLVED, self.Status.CLOSED) and self.due_at < timezone.now()


class Interaction(ImmutableRecord):
    class Kind(models.TextChoices):
        CALL_OUT = "call_out", "تماس گرفتیم"
        CALL_IN = "call_in", "تماس گرفت"
        NO_ANSWER = "no_answer", "پاسخ نداد"
        SMS = "sms", "پیامک"
        VISIT = "visit", "مراجعه حضوری"
        NOTE = "note", "یادداشت داخلی"

    request = models.ForeignKey(ConsultRequest, on_delete=models.PROTECT, related_name="interactions", verbose_name="درخواست")
    kind = models.CharField("نوع", max_length=10, choices=Kind.choices)
    text = models.TextField("شرح", max_length=2000)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name="کارشناس")
    created_at = models.DateTimeField("زمان", auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "سابقه پیگیری"
        verbose_name_plural = "سوابق پیگیری"


def feedback_token():
    return secrets.token_urlsafe(16)


class Feedback(models.Model):
    order = models.OneToOneField(LabOrder, on_delete=models.CASCADE, related_name="feedback", verbose_name="پذیرش")
    token = models.CharField("توکن", max_length=32, unique=True, default=feedback_token, editable=False)
    sent_at = models.DateTimeField("ارسال", null=True, blank=True)
    submitted_at = models.DateTimeField("ثبت نظر", null=True, blank=True)
    overall = models.PositiveSmallIntegerField("رضایت کلی", null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(5)])
    waiting = models.PositiveSmallIntegerField("زمان انتظار", null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(5)])
    staff = models.PositiveSmallIntegerField("برخورد کارکنان", null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(5)])
    sampling = models.PositiveSmallIntegerField("نمونه‌گیری", null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.TextField("توضیح", max_length=1000, blank=True)
    wants_contact = models.BooleanField("درخواست تماس", default=False)

    class Meta:
        ordering = ["-submitted_at", "-id"]
        verbose_name = "نظرسنجی"
        verbose_name_plural = "نظرسنجی‌های رضایت"

    def __str__(self):
        return f"نظرسنجی {self.order.number}"

    @property
    def is_open(self):
        if self.submitted_at:
            return False
        days = settings.LAB_RULES["feedback_valid_days"]
        return self.sent_at is None or self.sent_at > timezone.now() - datetime.timedelta(days=days)


class RecallReminder(models.Model):
    """یادآوری تکرار آزمایش دوره‌ای (فقط برای بیمارانی که رضایت به یادآوری داده‌اند)."""

    item = models.OneToOneField(LabOrderItem, on_delete=models.CASCADE, related_name="recall", verbose_name="ردیف پذیرش")
    patient = models.ForeignKey(LabPatient, on_delete=models.CASCADE, related_name="recalls", verbose_name="بیمار")
    test = models.ForeignKey(LabTest, on_delete=models.CASCADE, related_name="+", verbose_name="آزمایش")
    due_date = models.DateField("موعد")
    sent_at = models.DateTimeField("ارسال", auto_now_add=True)

    class Meta:
        ordering = ["-sent_at"]
        verbose_name = "یادآوری آزمایش دوره‌ای"
        verbose_name_plural = "یادآوری‌های آزمایش دوره‌ای"

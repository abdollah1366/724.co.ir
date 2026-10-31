"""پذیرش (سفارش آزمایش)، پرداخت بیمار، و صورت‌حساب و تسویه بیمه."""

from django.db import models
from django.db.models import Sum

from apps.accounting.models import PaymentMethod
from apps.audit.documents import DocStatus, DocumentError, DocumentLine, PostedDocument

from .catalog import LabPanel, LabTest
from .patients import InsurancePlan, LabPatient, SamplingAppointment, SamplingSchedule


class LabOrder(PostedDocument):
    """پذیرش آزمایشگاه. پیش‌نویس قابل ویرایش است؛ با ثبت نهایی شماره پذیرش، نمونه‌ها و سند حسابداری ساخته می‌شود."""

    NUMBER_PREFIX = "LAB"

    class Priority(models.TextChoices):
        ROUTINE = "routine", "عادی"
        URGENT = "urgent", "فوری"

    class Progress(models.TextChoices):
        REGISTERED = "registered", "پذیرش شد"
        SAMPLING = "sampling", "در انتظار نمونه"
        IN_PROGRESS = "in_progress", "در حال انجام"
        PARTIAL = "partial", "جواب ناقص آماده"
        READY = "ready", "جواب آماده"
        DELIVERED = "delivered", "تحویل شد"

    class Delivery(models.TextChoices):
        ONLINE = "online", "آنلاین و حضوری"
        IN_PERSON = "in_person", "فقط حضوری"

    patient = models.ForeignKey(LabPatient, on_delete=models.PROTECT, related_name="orders", verbose_name="بیمار")
    appointment = models.OneToOneField(
        SamplingAppointment, on_delete=models.SET_NULL, null=True, blank=True, related_name="order", verbose_name="نوبت"
    )
    sampling_place = models.CharField("محل نمونه‌گیری", max_length=4, choices=SamplingSchedule.Kind.choices, default="lab")
    priority = models.CharField("اولویت", max_length=8, choices=Priority.choices, default=Priority.ROUTINE)
    referring_doctor = models.CharField("پزشک درخواست‌کننده", max_length=120, blank=True)
    referring_council_no = models.CharField("نظام پزشکی پزشک", max_length=20, blank=True)
    prescription_code = models.CharField("کد رهگیری نسخه", max_length=40, blank=True)
    basic_plan = models.ForeignKey(
        InsurancePlan, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name="بیمه پایه",
        limit_choices_to={"kind": InsurancePlan.Kind.BASIC},
    )
    basic_insurance_no = models.CharField("شماره بیمه پایه", max_length=30, blank=True)
    basic_approval_code = models.CharField("کد تأیید بیمه پایه", max_length=40, blank=True)
    supplementary_plan = models.ForeignKey(
        InsurancePlan, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name="بیمه تکمیلی",
        limit_choices_to={"kind": InsurancePlan.Kind.SUPPLEMENTARY},
    )
    supplementary_insurance_no = models.CharField("شماره بیمه تکمیلی", max_length=30, blank=True)
    supplementary_approval_code = models.CharField("کد تأیید بیمه تکمیلی", max_length=40, blank=True)
    home_fee = models.PositiveBigIntegerField("هزینه نمونه‌گیری در منزل (ریال)", default=0)
    discount = models.PositiveBigIntegerField("تخفیف (ریال)", default=0)
    discount_reason = models.CharField("علت تخفیف", max_length=200, blank=True)
    delivery = models.CharField("نحوه تحویل جواب", max_length=10, choices=Delivery.choices, default=Delivery.ONLINE)
    clinical_note = models.CharField(
        "نکته بالینی لازم برای آزمایش", max_length=300, blank=True, help_text="فقط در حد نیاز آزمایش؛ مثل داروی ضدانعقاد یا بارداری"
    )
    # جمع‌ها (توسط services.pricing محاسبه می‌شوند)
    gross_amount = models.PositiveBigIntegerField("جمع تعرفه (ریال)", default=0, editable=False)
    basic_share = models.PositiveBigIntegerField("سهم بیمه پایه", default=0, editable=False)
    supplementary_share = models.PositiveBigIntegerField("سهم بیمه تکمیلی", default=0, editable=False)
    patient_share = models.PositiveBigIntegerField("سهم بیمار پیش از تخفیف", default=0, editable=False)
    progress = models.CharField("وضعیت کار", max_length=12, choices=Progress.choices, default=Progress.REGISTERED, editable=False)
    report_code = models.CharField("کد اصالت برگه جواب", max_length=12, unique=True, null=True, blank=True, editable=False)
    delivered_at = models.DateTimeField("زمان تحویل", null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "پذیرش آزمایشگاه"
        verbose_name_plural = "پذیرش‌های آزمایشگاه"
        permissions = [
            ("post_laborder", "ثبت نهایی پذیرش آزمایشگاه"),
            ("reverse_laborder", "ابطال پذیرش آزمایشگاه"),
            ("give_lab_discount", "اعمال تخفیف در پذیرش"),
            ("deliver_lab_report", "تحویل جواب آزمایش"),
        ]

    @property
    def net_patient(self):
        return max(self.patient_share - self.discount, 0)

    @property
    def paid_amount(self):
        rows = self.payments.filter(status=DocStatus.POSTED).values("direction").annotate(total=Sum("amount"))
        totals = {r["direction"]: r["total"] for r in rows}
        return totals.get(LabPayment.Direction.RECEIVE, 0) - totals.get(LabPayment.Direction.REFUND, 0)

    @property
    def balance_due(self):
        return self.net_patient - self.paid_amount

    def set_progress(self, progress):
        self.progress = progress
        self._save_status(["progress"])

    def validate_for_post(self):
        from ..services import orders

        orders.validate_order(self)

    def perform_post(self, user):
        from ..services import orders

        orders.on_order_posted(self, user)

    def validate_for_reverse(self):
        from ..services import orders

        orders.validate_order_reverse(self)

    def perform_reverse(self, user):
        from apps.accounting import services as acc

        acc.reverse_entry_for(self, user)


class LabOrderItem(DocumentLine):
    DOCUMENT_FIELD = "order"

    order = models.ForeignKey(LabOrder, on_delete=models.CASCADE, related_name="items", verbose_name="پذیرش")
    test = models.ForeignKey(LabTest, on_delete=models.PROTECT, related_name="order_items", verbose_name="آزمایش")
    panel = models.ForeignKey(LabPanel, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name="بسته")
    price = models.PositiveBigIntegerField("تعرفه (ریال)", default=0)
    basic_share = models.PositiveBigIntegerField("سهم بیمه پایه", default=0)
    supplementary_share = models.PositiveBigIntegerField("سهم بیمه تکمیلی", default=0)
    patient_share = models.PositiveBigIntegerField("سهم بیمار", default=0)
    basic_covered = models.BooleanField("تحت پوشش بیمه پایه", default=False)
    needs_approval = models.BooleanField("نیاز به تأییدیه بیمه", default=False)

    class Meta:
        ordering = ["test__section__order", "test__order"]
        verbose_name = "آزمایش پذیرش"
        verbose_name_plural = "آزمایش‌های پذیرش"
        constraints = [models.UniqueConstraint(fields=["order", "test"], name="unique_test_per_lab_order")]

    def __str__(self):
        return str(self.test)


class LabPayment(PostedDocument):
    NUMBER_PREFIX = "LPY"

    class Direction(models.TextChoices):
        RECEIVE = "receive", "دریافت از بیمار"
        REFUND = "refund", "استرداد به بیمار"

    order = models.ForeignKey(LabOrder, on_delete=models.PROTECT, related_name="payments", verbose_name="پذیرش")
    direction = models.CharField("نوع", max_length=8, choices=Direction.choices, default=Direction.RECEIVE)
    method = models.CharField("روش", max_length=8, choices=[c for c in PaymentMethod.choices if c[0] != "cheque"])
    amount = models.PositiveBigIntegerField("مبلغ (ریال)")
    reference_no = models.CharField("شماره پیگیری", max_length=40, blank=True)
    client_token = models.CharField("شناسه یکتای درخواست", max_length=40, unique=True, null=True, blank=True, editable=False)
    cash_session = models.ForeignKey("sales.CashSession", on_delete=models.PROTECT, null=True, blank=True, editable=False,
                                     related_name="lab_payments", verbose_name="شیفت صندوق")

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "دریافت / استرداد آزمایشگاه"
        verbose_name_plural = "دریافت‌ها و استردادهای آزمایشگاه"
        permissions = [("post_labpayment", "ثبت دریافت آزمایشگاه"), ("reverse_labpayment", "برگشت دریافت آزمایشگاه")]

    def validate_for_post(self):
        from ..services import orders

        orders.validate_payment(self)

    def perform_post(self, user):
        from ..services import orders

        orders.post_payment(self, user)

    def validate_for_reverse(self):
        if self.direction == self.Direction.RECEIVE and self.order.paid_amount - self.amount < 0:
            raise DocumentError("پیش از برگشت این دریافت، استردادهای ثبت‌شده را برگشت بزنید.")

    def perform_reverse(self, user):
        from apps.accounting import services as acc

        acc.reverse_entry_for(self, user)


class InsuranceClaim(PostedDocument):
    """صورت‌حساب ارسالی به بیمه برای یک دوره. ثبت نهایی = ارسال؛ اثر مالی ندارد (طلب هنگام پذیرش ثبت شده است)."""

    NUMBER_PREFIX = "LCL"

    plan = models.ForeignKey(InsurancePlan, on_delete=models.PROTECT, related_name="claims", verbose_name="قرارداد")
    period_start = models.DateField("از تاریخ")
    period_end = models.DateField("تا تاریخ")

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "صورت‌حساب بیمه"
        verbose_name_plural = "صورت‌حساب‌های بیمه"
        permissions = [("post_insuranceclaim", "ارسال صورت‌حساب بیمه"), ("reverse_insuranceclaim", "برگشت صورت‌حساب بیمه")]

    @property
    def total(self):
        return self.lines.aggregate(t=Sum("amount"))["t"] or 0

    @property
    def settled_total(self):
        agg = self.settlements.filter(status=DocStatus.POSTED).aggregate(r=Sum("received"), d=Sum("deductions"))
        return (agg["r"] or 0) + (agg["d"] or 0)

    def validate_for_post(self):
        if not self.lines.exists():
            raise DocumentError("صورت‌حساب ردیفی ندارد.")

    def perform_post(self, user):
        pass

    def validate_for_reverse(self):
        if self.settlements.filter(status=DocStatus.POSTED).exists():
            raise DocumentError("برای این صورت‌حساب تسویه ثبت شده است؛ ابتدا تسویه را برگشت بزنید.")

    def perform_reverse(self, user):
        pass


class ClaimLine(DocumentLine):
    DOCUMENT_FIELD = "claim"

    claim = models.ForeignKey(InsuranceClaim, on_delete=models.CASCADE, related_name="lines", verbose_name="صورت‌حساب")
    order = models.ForeignKey(LabOrder, on_delete=models.PROTECT, related_name="claim_lines", verbose_name="پذیرش")
    amount = models.PositiveBigIntegerField("مبلغ درخواستی (ریال)")

    class Meta:
        verbose_name = "ردیف صورت‌حساب بیمه"
        verbose_name_plural = "ردیف‌های صورت‌حساب بیمه"
        constraints = [models.UniqueConstraint(fields=["claim", "order"], name="unique_order_per_claim")]


class ClaimSettlement(PostedDocument):
    """تسویه بیمه: مبلغ دریافتی + کسورات = بستانکار حساب طلب از بیمه."""

    NUMBER_PREFIX = "LCS"

    claim = models.ForeignKey(InsuranceClaim, on_delete=models.PROTECT, related_name="settlements", verbose_name="صورت‌حساب")
    received = models.PositiveBigIntegerField("مبلغ دریافتی (ریال)", default=0)
    deductions = models.PositiveBigIntegerField("کسورات بیمه (ریال)", default=0)
    method = models.CharField("روش دریافت", max_length=8, choices=PaymentMethod.choices, default=PaymentMethod.BANK)
    reference_no = models.CharField("شماره پیگیری", max_length=40, blank=True)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "تسویه بیمه"
        verbose_name_plural = "تسویه‌های بیمه"
        permissions = [("post_claimsettlement", "ثبت تسویه بیمه"), ("reverse_claimsettlement", "برگشت تسویه بیمه")]

    def validate_for_post(self):
        from ..services import orders

        orders.validate_settlement(self)

    def perform_post(self, user):
        from ..services import orders

        orders.post_settlement(self, user)

    def perform_reverse(self, user):
        from apps.accounting import services as acc

        acc.reverse_entry_for(self, user)


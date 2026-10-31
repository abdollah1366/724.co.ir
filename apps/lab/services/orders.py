"""پذیرش، پرداخت بیمار و بیمه آزمایشگاه: اعتبارسنجی و ثبت حسابداری."""

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounting import services as acc
from apps.audit.documents import DocStatus, DocumentError

from ..models import (
    ClaimLine, InsuranceClaim, InsurancePlan, LabOrder, LabOrderItem, LabPayment, LabResult, SamplingAppointment, random_code,
)
from . import lis, pricing

LAB_REVENUE = "4105"
LAB_DISCOUNT = "4106"
INSURANCE_DEDUCTIONS = "6106"


# --- پیش‌نویس پذیرش ---
@transaction.atomic
def set_order_tests(order, tests=(), panels=()):
    """آزمایش‌های پیش‌نویس را جایگزین و سهم‌ها را دوباره محاسبه می‌کند."""
    if not order.is_draft:
        raise DocumentError("پذیرش ثبت نهایی شده و آزمایش‌های آن قابل تغییر نیست.")
    selection = pricing.expand_selection(tests, panels)
    order.items.all().delete()
    lines = pricing.price_lines(selection, order.basic_plan, order.supplementary_plan)
    LabOrderItem.objects.bulk_create(
        LabOrderItem(
            order=order, test=line.test, panel=line.panel, price=line.price, basic_share=line.basic_share,
            supplementary_share=line.supplementary_share, patient_share=line.patient_share,
            basic_covered=line.basic_covered, needs_approval=line.needs_approval,
        )
        for line in lines
    )
    _store_totals(order, lines)
    return order


def reprice(order):
    """محاسبه مجدد پس از تغییر بیمه یا هزینه منزل (همان آزمایش‌ها)."""
    items = list(order.items.select_related("test", "panel"))
    tests = [i.test for i in items if i.panel_id is None]
    panels = {i.panel_id: i.panel for i in items if i.panel_id}.values()
    return set_order_tests(order, tests, panels)


def _store_totals(order, lines):
    result = pricing.totals(lines, order.home_fee, order.supplementary_plan)
    order.gross_amount = result["gross"]
    order.basic_share = result["basic"]
    order.supplementary_share = result["supplementary"]
    order.patient_share = result["patient"]
    order.save(update_fields=["gross_amount", "basic_share", "supplementary_share", "patient_share"])


def _plan_check(order, plan, insurance_no, approval_code, label):
    if plan is None:
        return
    if not plan.is_valid_on(order.date):
        raise DocumentError(f"قرارداد {plan} در تاریخ پذیرش معتبر نیست.")
    if not insurance_no.strip():
        raise DocumentError(f"شماره {label} را وارد کنید.")
    needs = plan.requires_approval_code or plan.rules.filter(
        requires_approval=True, test__in=order.items.values("test")
    ).exists()
    if needs and not approval_code.strip():
        raise DocumentError(f"کد تأیید {label} برای این پذیرش الزامی است.")


def _prices_current(order, items):
    selection = [(i.test, i.panel) for i in items]
    lines = pricing.price_lines(selection, order.basic_plan, order.supplementary_plan)
    fields = ("price", "basic_share", "supplementary_share", "patient_share")
    by_test = {line.test.pk: line for line in lines}
    for item in items:
        line = by_test[item.test_id]
        if any(getattr(item, f) != getattr(line, f) for f in fields):
            return False
    expected = pricing.totals(lines, order.home_fee, order.supplementary_plan)
    return (order.gross_amount, order.basic_share, order.supplementary_share, order.patient_share) == (
        expected["gross"], expected["basic"], expected["supplementary"], expected["patient"]
    )


def validate_order(order):
    items = list(order.items.select_related("test", "panel"))
    if not items:
        raise DocumentError("هیچ آزمایشی برای پذیرش انتخاب نشده است.")
    inactive = [i.test.name for i in items if not i.test.is_active]
    if inactive:
        raise DocumentError(f"آزمایش غیرفعال: {'، '.join(inactive)}")
    if order.basic_plan and order.basic_plan.kind != InsurancePlan.Kind.BASIC:
        raise DocumentError("قرارداد انتخاب‌شده برای بیمه پایه از نوع پایه نیست.")
    if order.supplementary_plan and order.supplementary_plan.kind != InsurancePlan.Kind.SUPPLEMENTARY:
        raise DocumentError("قرارداد انتخاب‌شده برای بیمه تکمیلی از نوع تکمیلی نیست.")
    _plan_check(order, order.basic_plan, order.basic_insurance_no, order.basic_approval_code, "بیمه پایه")
    _plan_check(order, order.supplementary_plan, order.supplementary_insurance_no, order.supplementary_approval_code, "بیمه تکمیلی")
    # سهم‌های ذخیره‌شده باید با قرارداد و تعرفه فعلی یکی باشد (اگر بیمه، تعرفه یا هزینه منزل عوض شده، دوباره محاسبه شود).
    if not _prices_current(order, items):
        raise DocumentError("مبالغ پذیرش به‌روز نیست؛ «محاسبه مجدد» را بزنید.")
    if order.discount > order.patient_share:
        raise DocumentError("تخفیف بیشتر از سهم بیمار است.")
    if order.discount and not order.discount_reason.strip():
        raise DocumentError("علت تخفیف را وارد کنید.")
    if order.sampling_place == "home" and not order.patient.address:
        raise DocumentError("برای نمونه‌گیری در منزل آدرس بیمار را ثبت کنید.")
    home_only = [i.test.name for i in items if order.sampling_place == "home" and not i.test.home_sampling]
    if home_only:
        raise DocumentError(f"این آزمایش‌ها در منزل انجام نمی‌شوند: {'، '.join(home_only)}")


def on_order_posted(order, user):
    order.report_code = _unique_report_code()
    order._save_status(["report_code"])
    lis.create_specimens_and_results(order)
    order.set_progress(LabOrder.Progress.SAMPLING)
    if order.appointment_id:
        SamplingAppointment.objects.filter(pk=order.appointment_id).update(status=SamplingAppointment.Status.ARRIVED)
    _post_order_entry(order, user)


def _unique_report_code():
    while True:
        code = random_code(10)
        if not LabOrder.objects.filter(report_code=code).exists():
            return code


def _post_order_entry(order, user):
    text = f"پذیرش آزمایشگاه {order.number} - {order.patient}"
    lines = [
        (acc.AR_INSURERS, order.basic_share, 0, order.basic_plan.insurer if order.basic_plan else None, f"سهم بیمه پایه {order.number}"),
        (acc.AR_INSURERS, order.supplementary_share, 0,
         order.supplementary_plan.insurer if order.supplementary_plan else None, f"سهم بیمه تکمیلی {order.number}"),
        (acc.AR_CUSTOMERS, order.net_patient, 0, None, f"سهم بیمار {order.number}"),
        (LAB_DISCOUNT, order.discount, 0, None, f"تخفیف: {order.discount_reason}"),
        (LAB_REVENUE, 0, order.gross_amount, None, text),
    ]
    acc.post_entry(acc.source_of(order), lines, text, order.date, user)


def validate_order_reverse(order):
    if LabResult.objects.filter(item__order=order, status=LabResult.Status.APPROVED).exists():
        raise DocumentError("برای این پذیرش جواب تأییدشده وجود دارد و قابل ابطال نیست.")
    if order.paid_amount:
        raise DocumentError("ابتدا مبلغ دریافتی را به بیمار مسترد کنید (ثبت استرداد).")
    if ClaimLine.objects.filter(order=order, claim__status=DocStatus.POSTED).exists():
        raise DocumentError("این پذیرش در صورت‌حساب ارسالی بیمه است؛ ابتدا صورت‌حساب را برگشت بزنید.")


# --- دریافت از بیمار ---
def validate_payment(payment):
    order = LabOrder.objects.select_for_update().get(pk=payment.order_id)
    if order.status != DocStatus.POSTED:
        raise DocumentError("دریافت فقط برای پذیرش ثبت‌شده ممکن است.")
    if payment.amount <= 0:
        raise DocumentError("مبلغ باید بیشتر از صفر باشد.")
    if payment.method != "cash" and not payment.reference_no.strip():
        raise DocumentError("برای پرداخت کارتی یا بانکی شماره پیگیری الزامی است.")
    if payment.direction == LabPayment.Direction.RECEIVE and payment.amount > order.balance_due:
        raise DocumentError(f"مبلغ بیشتر از مانده قابل دریافت ({order.balance_due:,} ریال) است.")
    if payment.direction == LabPayment.Direction.REFUND and payment.amount > order.paid_amount:
        raise DocumentError("مبلغ استرداد بیشتر از مبلغ پرداختی بیمار است.")


def post_payment(payment, user):
    from apps.sales import cashbox

    money = acc.METHOD_ACCOUNT[payment.method]
    if payment.method in ("cash", "card"):
        # اگر برای آزمایشگاه صندوق تعریف شده، دریافت فقط در شیفت باز صندوق و به حساب همان صندوق.
        session = cashbox.require_session(user, cashbox.Department.LAB)
        if session is not None:
            money = cashbox.money_code(session, payment.method, money)
            LabPayment.objects.filter(pk=payment.pk).update(cash_session=session)
            payment.cash_session = session
    text = f"{payment.get_direction_display()} پذیرش {payment.order.number} {payment.reference_no}".strip()
    if payment.direction == LabPayment.Direction.RECEIVE:
        lines = [(money, payment.amount, 0, None, text), (acc.AR_CUSTOMERS, 0, payment.amount, None, text)]
    else:
        lines = [(acc.AR_CUSTOMERS, payment.amount, 0, None, text), (money, 0, payment.amount, None, text)]
    acc.post_entry(acc.source_of(payment), lines, text, payment.date, user)


def receive(order, user, amount, method="cash", reference_no="", direction=LabPayment.Direction.RECEIVE):
    payment = LabPayment.objects.create(
        order=order, amount=amount, method=method, reference_no=reference_no, direction=direction, created_by=user
    )
    try:
        payment.post(user)
    except DocumentError:
        payment.delete()
        raise
    return payment


# --- صورت‌حساب و تسویه بیمه ---
def claimable_orders(plan, start, end):
    share = "basic_share" if plan.kind == InsurancePlan.Kind.BASIC else "supplementary_share"
    plan_field = "basic_plan" if plan.kind == InsurancePlan.Kind.BASIC else "supplementary_plan"
    already = ClaimLine.objects.filter(claim__plan=plan).exclude(claim__status=DocStatus.REVERSED).values("order_id")
    return (
        LabOrder.objects.filter(status=DocStatus.POSTED, date__range=(start, end), **{plan_field: plan, f"{share}__gt": 0})
        .exclude(pk__in=already).select_related("patient"),
        share,
    )


@transaction.atomic
def build_claim(plan, start, end, user):
    orders, share = claimable_orders(plan, start, end)
    claim = InsuranceClaim.objects.create(plan=plan, period_start=start, period_end=end, created_by=user)
    ClaimLine.objects.bulk_create(ClaimLine(claim=claim, order=o, amount=getattr(o, share)) for o in orders)
    return claim


def validate_settlement(settlement):
    claim = InsuranceClaim.objects.select_for_update().get(pk=settlement.claim_id)
    if claim.status != DocStatus.POSTED:
        raise DocumentError("فقط صورت‌حساب ارسال‌شده قابل تسویه است.")
    amount = settlement.received + settlement.deductions
    if amount <= 0:
        raise DocumentError("مبلغ دریافتی یا کسورات را وارد کنید.")
    if amount > claim.total - claim.settled_total:
        raise DocumentError(f"جمع دریافتی و کسورات بیشتر از مانده صورت‌حساب ({claim.total - claim.settled_total:,} ریال) است.")
    if settlement.received and settlement.method != "cash" and not settlement.reference_no.strip():
        raise DocumentError("شماره پیگیری واریز بیمه را وارد کنید.")


def post_settlement(settlement, user):
    claim = settlement.claim
    insurer = claim.plan.insurer
    text = f"تسویه صورت‌حساب {claim.number} {claim.plan}"
    lines = [
        (acc.METHOD_ACCOUNT[settlement.method], settlement.received, 0, None, text),
        (INSURANCE_DEDUCTIONS, settlement.deductions, 0, None, f"کسورات {claim.number} {insurer}"),
        (acc.AR_INSURERS, 0, settlement.received + settlement.deductions, insurer, text),
    ]
    acc.post_entry(acc.source_of(settlement), lines, text, settlement.date, user)


def patient_orders(patient):
    return patient.orders.filter(~Q(status=DocStatus.DRAFT)).order_by("-date", "-id")


def mark_delivered(order, user):
    if order.status != DocStatus.POSTED:
        raise DocumentError("پذیرش ثبت نهایی نشده است.")
    order.delivered_at = timezone.now()
    order.progress = LabOrder.Progress.DELIVERED
    order._save_status(["delivered_at", "progress"])

import datetime

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q, Sum
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.clinic.models import Doctor

from . import payments, services
from .forms import CloseForm, ConsultationForm, DeclineForm, MeetingLinkForm, MessageForm
from .models import ConsultAttachment, ConsultMessage, ConsultPayment, Consultation

Kind = Consultation.Kind
Role = ConsultMessage.Role


def _online_doctors():
    return (
        Doctor.objects.active().filter(online__is_enabled=True)
        .select_related("online", "specialty", "department").order_by("department__order", "last_name")
    )


def index(request):
    doctors = [d for d in _online_doctors() if d.online.offered_kinds()]
    return render(request, "telemed/index.html", {"doctors": doctors})


def _online_profile(slug):
    doctor = get_object_or_404(_online_doctors(), slug=slug)
    if not doctor.online.offered_kinds():
        raise Http404
    return doctor


def _parse_date(value):
    try:
        return datetime.date.fromisoformat(value or "")
    except ValueError:
        return None


def doctor(request, slug):
    doctor = _online_profile(slug)
    profile = doctor.online
    live = {Kind.CALL, Kind.VIDEO} & set(profile.offered_kinds())
    # فقط روزهایی که هنوز ساعت آزاد دارند (مثلاً امروز پس از پایان ساعت آنلاین نمی‌آید)
    dates = [day for day in profile.available_dates() if profile.available_slots(day)] if live else []
    selected = _parse_date(request.GET.get("date"))
    selected = selected if selected in dates else (dates[0] if dates else None)
    slots = profile.available_slots(selected) if selected else []
    return render(request, "telemed/doctor.html", {
        "doctor": doctor, "profile": profile, "dates": dates, "selected": selected, "slots": slots,
        "free_left": profile.free_left_today() if profile.free_enabled else 0,
    })


def _scheduled_at(request):
    try:
        value = datetime.datetime.fromisoformat(request.GET.get("slot") or request.POST.get("slot") or "")
    except ValueError:
        return None
    return timezone.make_aware(value) if timezone.is_naive(value) else value


def _create(request, *, kind, doctor=None):
    scheduled_at = _scheduled_at(request) if kind in Consultation.LIVE_KINDS else None
    if kind in Consultation.LIVE_KINDS and scheduled_at is None:
        messages.error(request, "زمان ویزیت را انتخاب کنید.")
        return redirect("telemed:doctor", slug=doctor.slug)
    form = ConsultationForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            consultation = services.create(
                request.user, kind=kind, doctor=doctor, scheduled_at=scheduled_at, data=form.payload(),
                files=form.cleaned_data["files"],
            )
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            if consultation.status == Consultation.Status.AWAITING_PAYMENT:
                return redirect("telemed:detail", code=consultation.code)
            messages.success(request, "درخواست شما ثبت شد. پاسخ را در همین صفحه می‌بینید.")
            return redirect("telemed:detail", code=consultation.code)
    fee = doctor.online.fee_for(kind) if doctor else 0
    return render(request, "telemed/new.html", {
        "form": form, "doctor": doctor, "kind": kind, "kind_label": Kind(kind).label, "fee": fee,
        "scheduled_at": scheduled_at, "slot": request.GET.get("slot") or request.POST.get("slot") or "",
        "online_payment": payments.gateway().online,
    })


@login_required
def new_doctor(request, slug, kind):
    doctor = _online_profile(slug)
    if kind not in doctor.online.offered_kinds():
        raise Http404
    return _create(request, kind=kind, doctor=doctor)


@login_required
def new_pharmacy(request):
    return _create(request, kind=Kind.FREE)


@login_required
def mine(request):
    consultations = request.user.consultations.select_related("doctor")
    return render(request, "telemed/mine.html", {"consultations": consultations})


def _get(request, code):
    consultation = get_object_or_404(Consultation.objects.select_related("doctor", "patient__profile"), code=code)
    role = services.role_for(request.user, consultation)
    if role is None:
        # همان پاسخ «پیدا نشد» تا وجود کد برای دیگران معلوم نشود
        raise Http404
    return consultation, role


@login_required
@never_cache
def detail(request, code):
    consultation, role = _get(request, code)
    if request.method == "POST":
        form = MessageForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                services.post_message(consultation, request.user, role, form.cleaned_data["text"], form.cleaned_data["files"])
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
            else:
                return redirect(reverse("telemed:detail", args=[code]) + "#last")
    else:
        form = MessageForm()
        if role == Role.PROVIDER:
            services.log_view(consultation)
    can_post, cannot_reason = services.can_post(consultation, role)
    context = {
        "c": consultation, "role": role, "is_provider": role == Role.PROVIDER, "form": form,
        "thread": consultation.messages.select_related("sender").prefetch_related("attachments"),
        "can_post": can_post, "cannot_reason": cannot_reason, "can_cancel": services.can_cancel(consultation),
        "online_payment": payments.gateway().online, "payment": consultation.paid_payment(),
    }
    if role == Role.PROVIDER:
        context.update(close_form=CloseForm(), decline_form=DeclineForm(), link_form=MeetingLinkForm(
            initial={"meeting_link": consultation.meeting_link}
        ), patient_mobile=getattr(getattr(consultation.patient, "profile", None), "mobile", ""))
    return render(request, "telemed/detail.html", context)


@login_required
@never_cache
def attachment(request, pk):
    item = get_object_or_404(ConsultAttachment.objects.select_related("message__consultation"), pk=pk)
    consultation = item.message.consultation
    role = services.role_for(request.user, consultation)
    if role is None:
        raise Http404
    if role == Role.PROVIDER:
        services.log_view(consultation, f"باز کردن پیوست {item.pk}")
    try:
        handle = item.file.open("rb")
    except FileNotFoundError:
        raise Http404("فایل پیدا نشد.")
    response = FileResponse(handle, content_type=item.content_type)
    extension = item.file.name.rsplit(".", 1)[-1]
    response["Content-Disposition"] = f'inline; filename="consult-{item.pk}.{extension}"'
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _provider_action(request, code, form_class, action, success):
    consultation, role = _get(request, code)
    if role != Role.PROVIDER:
        raise PermissionDenied
    form = form_class(request.POST)
    if form.is_valid():
        try:
            action(consultation, request.user, *form.cleaned_data.values())
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, success)
    else:
        messages.error(request, " ".join(e for errors in form.errors.values() for e in errors))
    return redirect("telemed:detail", code=code)


@login_required
@require_POST
def close(request, code):
    return _provider_action(request, code, CloseForm, services.close, "مشاوره بسته شد.")


@login_required
@require_POST
def decline(request, code):
    return _provider_action(request, code, DeclineForm, services.decline, "مشاوره رد شد و به بیمار اطلاع داده شد.")


@login_required
@require_POST
def meeting_link(request, code):
    return _provider_action(request, code, MeetingLinkForm, services.set_meeting_link, "پیوند تماس ثبت شد.")


@login_required
@require_POST
def cancel(request, code):
    consultation, role = _get(request, code)
    if role != Role.PATIENT:
        raise PermissionDenied
    try:
        services.cancel(consultation, request.user)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, "مشاوره لغو شد.")
    return redirect("telemed:detail", code=code)


# ---------------------------------------------------------------- پرداخت


@login_required
@require_POST
def pay(request, code):
    consultation, role = _get(request, code)
    if role != Role.PATIENT:
        raise PermissionDenied
    try:
        url = services.start_payment(
            consultation, lambda authority: request.build_absolute_uri(reverse("telemed:pay_callback", args=[authority]))
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("telemed:detail", code=code)
    return redirect(url)


@login_required
def pay_callback(request, authority):
    payment = get_object_or_404(ConsultPayment.objects.select_related("consultation"), authority=authority)
    if payment.consultation.patient_id != request.user.pk:
        raise Http404
    payment = services.complete_payment(authority, request)
    if payment.status == ConsultPayment.Status.PAID:
        messages.success(request, "پرداخت انجام شد و درخواست شما برای پاسخ ارسال شد.")
    elif payment.status == ConsultPayment.Status.REFUND_DUE:
        messages.error(request, "پرداخت پس از پایان مهلت رسید؛ مبلغ به شما برگردانده می‌شود.")
    else:
        messages.error(request, "پرداخت انجام نشد. دوباره تلاش کنید.")
    return redirect("telemed:detail", code=payment.consultation.code)


@login_required
def sandbox_pay(request, authority):
    if settings.PAYMENT_GATEWAY != "sandbox":
        raise Http404
    payment = get_object_or_404(ConsultPayment, authority=authority, status=ConsultPayment.Status.PENDING)
    if payment.consultation.patient_id != request.user.pk:
        raise Http404
    return render(request, "telemed/sandbox_pay.html", {
        "payment": payment, "callback": reverse("telemed:pay_callback", args=[authority]),
    })


# ---------------------------------------------------------------- کارتابل پاسخ‌دهنده و گزارش


@staff_member_required
@never_cache
def inbox(request):
    user = request.user
    if not (user.has_perm("telemed.answer_consultation") or user.has_perm("telemed.answer_pharmacy")):
        raise PermissionDenied
    base = Consultation.objects.visible_to_provider(user).select_related("doctor")
    show = request.GET.get("show", "open")
    if show == "closed":
        items = base.exclude(status__in=Consultation.OPEN_STATUSES)[:100]
    else:
        items = base.open().order_by("scheduled_at", "submitted_at")
    today = timezone.localdate()
    return render(request, "telemed/inbox.html", {
        "items": items, "show": show,
        "waiting": base.filter(status=Consultation.Status.WAITING).count(),
        "today_live": base.open().filter(scheduled_at__date=today).count(),
        "is_doctor": getattr(user, "doctor", None) is not None and user.has_perm("telemed.answer_consultation"),
        "is_pharmacist": user.has_perm("telemed.answer_pharmacy"),
    })


@staff_member_required
@permission_required("telemed.view_telemed_report", raise_exception=True)
def report(request):
    today = timezone.localdate()
    start = _parse_date(request.GET.get("from")) or today - datetime.timedelta(days=30)
    end = _parse_date(request.GET.get("to")) or today
    qs = Consultation.objects.filter(created_at__date__gte=start, created_at__date__lte=end)
    by_kind = qs.values("channel", "kind").annotate(
        total=Count("id"),
        closed=Count("id", filter=Q(status=Consultation.Status.CLOSED)),
        expired=Count("id", filter=Q(status=Consultation.Status.EXPIRED)),
    ).order_by("channel", "kind")
    labels = dict(Kind.choices)
    channels = dict(Consultation.Channel.choices)
    rows = [{**row, "kind_label": labels[row["kind"]], "channel_label": channels[row["channel"]]} for row in by_kind]
    pay = ConsultPayment.objects.filter(paid_at__date__gte=start, paid_at__date__lte=end)
    by_doctor = qs.filter(channel=Consultation.Channel.DOCTOR).values("doctor__first_name", "doctor__last_name").annotate(
        total=Count("id"), answered=Count("id", filter=Q(answered_at__isnull=False))
    ).order_by("-total")
    return render(request, "telemed/report.html", {
        "start": start, "end": end, "rows": rows, "by_doctor": by_doctor,
        "income": pay.filter(status=ConsultPayment.Status.PAID).aggregate(s=Sum("amount"))["s"] or 0,
        "refund_due": ConsultPayment.objects.filter(status=ConsultPayment.Status.REFUND_DUE).aggregate(s=Sum("amount"), n=Count("id")),
        "refunded": pay.filter(status=ConsultPayment.Status.REFUNDED).aggregate(s=Sum("amount"))["s"] or 0,
    })

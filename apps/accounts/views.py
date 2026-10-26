from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.utils.http import url_has_allowed_host_and_scheme

from apps.appointments.models import Appointment

from . import club as club_rules
from .forms import SignupForm
from .models import Profile, Reward


def signup(request):
    if request.user.is_authenticated:
        return redirect("accounts:dashboard")
    initial = {"referral_code": request.GET.get("ref", "")}
    form = SignupForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(request, "عضویت شما در باشگاه مشتریان ۷۲۴ انجام شد. خوش آمدید!")
        next_url = request.GET.get("next", "")
        if url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            return redirect(next_url)
        return redirect("accounts:dashboard")
    return render(request, "accounts/signup.html", {"form": form})


@login_required
def dashboard(request):
    profile, _ = Profile.objects.get_or_create(user=request.user)
    # فقط نوبت‌هایی که با همین حساب گرفته شده‌اند؛ تطبیق با شماره موبایل تا پیاده‌سازی
    # تأیید پیامکی موبایل انجام نمی‌شود تا کسی با شماره دیگران به سوابق آن‌ها دسترسی پیدا نکند.
    appointments = Appointment.objects.select_related("doctor").filter(user=request.user)[:10]
    return render(
        request,
        "accounts/dashboard.html",
        {
            "profile": profile,
            "points": profile.points,
            "level": club_rules.level_for(request.user),
            "rewards": Reward.objects.filter(is_active=True),
            "redemptions": request.user.redemptions.select_related("reward")[:10],
            "transactions": request.user.point_transactions.all()[:20],
            "referral_url": profile.referral_url(request),
            "referral_count": profile.referrals.count(),
            "appointments": appointments,
            "orders": request.user.orders.all()[:10],
            "questions": request.user.pharmacy_questions.select_related("product")[:10],
            "consultations": request.user.consultations.select_related("doctor")[:10],
            # لینک «سهم من» برای پزشکان و «فیش حقوق» برای کارکنان دارای پرونده حقوقی
            "is_provider": hasattr(request.user, "doctor"),
            "has_payroll": hasattr(request.user, "payroll_profile"),
        },
    )


def club(request):
    return render(
        request,
        "accounts/club.html",
        {"rules": settings.CLUB_RULES, "levels": settings.CLUB_LEVELS, "rewards": Reward.objects.filter(is_active=True)},
    )


@login_required
@require_POST
def redeem(request, pk):
    reward = get_object_or_404(Reward, pk=pk, is_active=True)
    Profile.objects.get_or_create(user=request.user)
    try:
        redemption = club_rules.redeem(request.user, reward)
    except club_rules.NotEnoughPoints:
        messages.error(request, "ژتون شما برای این جایزه کافی نیست.")
    else:
        messages.success(request, f"جایزه «{reward.title}» صادر شد. کد جایزه: {redemption.code} — آن را در پذیرش ارائه دهید.")
    return redirect("accounts:dashboard")

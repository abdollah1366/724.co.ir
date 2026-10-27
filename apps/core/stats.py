"""آمار بازدید سایت (بی‌نام) و آمار عمومی صفحه اول.

هر صفحه‌ای که بازدیدکننده عادی باز می‌کند یک «بازدید صفحه» است. «بازدیدکننده» یعنی مرورگری که
امروز اولین بار آمده؛ این را با یک کوکی که فقط تاریخ امروز را دارد می‌فهمیم: نه IP ذخیره می‌شود،
نه شناسه کاربر. بازدید کارکنان، ربات‌ها و پنل مدیریت شمرده نمی‌شود.
"""

import datetime
import re

from django.core.cache import cache
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import F, Sum
from django.utils import timezone

from .models import DailyVisit

VISIT_COOKIE = "v724"
STATS_CACHE_KEY = "core:site_stats"
STATS_CACHE_SECONDS = 300
BOT_RE = re.compile(r"bot|crawl|spider|slurp|preview|monitor|curl|wget|python|headless|lighthouse", re.I)
SKIP_NAMESPACES = {"admin"}


def is_countable(request, response):
    if request.method != "GET" or response.status_code != 200:
        return False
    if not response.get("Content-Type", "").startswith("text/html"):
        return False
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated and user.is_staff:
        return False
    match = getattr(request, "resolver_match", None)
    if match is None or match.namespace in SKIP_NAMESPACES:
        return False
    return not BOT_RE.search(request.META.get("HTTP_USER_AGENT", "") or "bot")


def record_visit(request, response):
    """یک بازدید صفحه ثبت می‌کند و اگر مرورگر امروز تازه آمده، کوکی روز را می‌گذارد."""
    today = timezone.localdate()
    new_visitor = request.COOKIES.get(VISIT_COOKIE) != today.isoformat()
    try:
        with transaction.atomic():
            updated = DailyVisit.objects.filter(date=today).update(
                page_views=F("page_views") + 1, visitors=F("visitors") + int(new_visitor))
            if not updated:
                try:
                    with transaction.atomic():
                        DailyVisit.objects.create(date=today, page_views=1, visitors=int(new_visitor))
                except IntegrityError:  # درخواست هم‌زمان همین لحظه ردیف امروز را ساخت
                    DailyVisit.objects.filter(date=today).update(
                        page_views=F("page_views") + 1, visitors=F("visitors") + int(new_visitor))
    except DatabaseError:
        return  # شمارش بازدید هرگز نباید صفحه را خراب کند
    if new_visitor:
        now = timezone.localtime()
        midnight = datetime.datetime.combine(today + datetime.timedelta(days=1), datetime.time(), now.tzinfo)
        response.set_cookie(VISIT_COOKIE, today.isoformat(), max_age=int((midnight - now).total_seconds()) + 1,
                            samesite="Lax", httponly=True, secure=request.is_secure())


def visit_totals():
    today = timezone.localdate()
    row = DailyVisit.objects.filter(date=today).first()
    totals = DailyVisit.objects.aggregate(views=Sum("page_views"), visitors=Sum("visitors"))
    return {
        "today_views": row.page_views if row else 0,
        "today_visitors": row.visitors if row else 0,
        "total_views": totals["views"] or 0,
        "total_visitors": totals["visitors"] or 0,
    }


def visit_series(days=14):
    """بازدید روزهای اخیر برای نمودار پیشخوان؛ روز بی‌بازدید صفر است."""
    today = timezone.localdate()
    start = today - datetime.timedelta(days=days - 1)
    rows = {r.date: r for r in DailyVisit.objects.filter(date__gte=start)}
    series = []
    for i in range(days):
        day = start + datetime.timedelta(days=i)
        row = rows.get(day)
        series.append({"date": day, "views": row.page_views if row else 0, "visitors": row.visitors if row else 0})
    peak = max((d["views"] for d in series), default=0) or 1
    for d in series:
        d["percent"] = round(d["views"] * 100 / peak)
    return series


def site_stats():
    """آمار عمومی صفحه اول؛ پنج دقیقه در کش می‌ماند تا هر بازدید چند پرس‌وجو نسازد."""
    stats = cache.get(STATS_CACHE_KEY)
    if stats is not None:
        return stats
    from apps.appointments.models import Appointment
    from apps.blog.models import Post
    from apps.clinic.models import Department, Doctor
    from apps.lab.models import LabTest
    from apps.shop.models import Product

    stats = visit_totals()
    stats.update({
        "doctors": Doctor.objects.active().count(),
        "departments": Department.objects.filter(is_active=True).count(),
        "lab_tests": LabTest.objects.public().count(),
        "products": Product.objects.available().count(),
        "articles": Post.objects.published().count(),
        "appointments": Appointment.objects.count(),
    })
    cache.set(STATS_CACHE_KEY, stats, STATS_CACHE_SECONDS)
    return stats

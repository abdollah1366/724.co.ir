from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.static import serve

from apps.blog.models import Post
from apps.clinic.models import Department

from . import stats
from .forms import ContactForm
from .models import ContactMessage


def home(request):
    departments = Department.objects.filter(is_active=True)
    latest_posts = Post.objects.published().select_related("category")[:3]
    return render(request, "core/home.html", {"departments": departments, "latest_posts": latest_posts})


@staff_member_required
def staff_home(request):
    """صفحه اول پیشخوان کارکنان: دسترسی سریع، آمار بازدید سایت و همه پنل‌هایی که کاربر اجازه دارد."""
    context = {"visits": stats.visit_totals(), "series": stats.visit_series(14)}
    if request.user.has_perm("core.view_contactmessage"):
        context["unread_messages"] = ContactMessage.objects.filter(is_read=False).count()
    if request.user.has_perm("appointments.view_appointment"):
        from apps.appointments.models import Appointment

        context["today_appointments"] = Appointment.objects.filter(date=timezone.localdate()).exclude(
            status=Appointment.Status.CANCELLED).count()
    return render(request, "staff/home.html", context)


def about(request):
    return render(request, "core/about.html")


def privacy(request):
    return render(request, "core/privacy.html")


def contact(request):
    form = ContactForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "پیام شما ثبت شد. به‌زودی با شما تماس می‌گیریم.")
        return redirect("core:contact")
    return render(request, "core/contact.html", {"form": form})


def public_media(request, path):
    """تصاویر عمومی روی هاست اشتراکی (DJANGO_SERVE_MEDIA). CSP جلوی اجرای اسکریپت در فایل بارگذاری‌شده را می‌گیرد."""
    response = serve(request, path, document_root=settings.MEDIA_ROOT)
    response["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox"
    response["Cache-Control"] = "public, max-age=604800"
    return response

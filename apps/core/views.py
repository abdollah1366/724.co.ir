from django.contrib import messages
from django.shortcuts import redirect, render

from apps.clinic.models import Department

from .forms import ContactForm


def home(request):
    departments = Department.objects.filter(is_active=True)
    return render(request, "core/home.html", {"departments": departments})


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

from django.db.models import Q
from django.shortcuts import get_object_or_404, render

from .models import Doctor, Floor, Service, Specialty


def floor_detail(request, slug):
    floor = get_object_or_404(Floor.objects.filter(is_active=True), slug=slug)
    return render(
        request,
        "clinic/floor_detail.html",
        {
            "floor": floor,
            "services": floor.services.filter(is_active=True),
            "doctors": floor.doctors.active().select_related("specialty"),
        },
    )


def doctor_list(request):
    doctors = Doctor.objects.active().select_related("specialty", "floor")
    specialty = request.GET.get("specialty")
    if specialty:
        doctors = doctors.filter(specialty__slug=specialty)
    return render(
        request,
        "clinic/doctor_list.html",
        {"doctors": doctors, "specialties": Specialty.objects.all(), "current_specialty": specialty},
    )


def doctor_detail(request, slug):
    doctor = get_object_or_404(
        Doctor.objects.active().select_related("specialty", "floor").prefetch_related("working_hours"), slug=slug
    )
    return render(request, "clinic/doctor_detail.html", {"doctor": doctor})


def search(request):
    q = request.GET.get("q", "").strip()[:100]
    results = {"doctors": [], "services": [], "floors": []}
    if q:
        results["doctors"] = Doctor.objects.active().filter(
            Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(specialty__name__icontains=q)
        ).select_related("specialty", "floor")
        results["services"] = Service.objects.filter(is_active=True).filter(
            Q(title__icontains=q) | Q(description__icontains=q)
        ).select_related("floor")
        results["floors"] = Floor.objects.filter(is_active=True).filter(
            Q(title__icontains=q) | Q(summary__icontains=q)
        )
    return render(request, "clinic/search.html", {"q": q, **results})

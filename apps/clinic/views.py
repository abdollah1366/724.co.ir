from django.db.models import Q
from django.shortcuts import get_object_or_404, render

from apps.shop.models import Product

from .models import Department, Doctor, Service, Specialty


def department_list(request):
    return render(request, "clinic/department_list.html", {"departments": Department.objects.filter(is_active=True)})


def department_detail(request, slug):
    department = get_object_or_404(Department.objects.filter(is_active=True), slug=slug)
    context = {
        "department": department,
        "services": department.services.filter(is_active=True),
        "doctors": department.doctors.active().select_related("specialty"),
    }
    if department.kind in (Department.Kind.PHARMACY, Department.Kind.EQUIPMENT):
        kind = Product.Kind.DRUG if department.kind == Department.Kind.PHARMACY else Product.Kind.EQUIPMENT
        context["product_kind"] = kind
        context["products"] = Product.objects.available().filter(kind=kind)[:8]
    return render(request, "clinic/department_detail.html", context)


def doctor_list(request):
    doctors = Doctor.objects.active().select_related("specialty", "department")
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
        Doctor.objects.active().select_related("specialty", "department").prefetch_related("working_hours"), slug=slug
    )
    return render(request, "clinic/doctor_detail.html", {"doctor": doctor})


def search(request):
    q = request.GET.get("q", "").strip()[:100]
    results = {"doctors": [], "services": [], "departments": [], "products": []}
    if q:
        results["doctors"] = Doctor.objects.active().filter(
            Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(specialty__name__icontains=q)
        ).select_related("specialty", "department")
        results["services"] = Service.objects.filter(is_active=True).filter(
            Q(title__icontains=q) | Q(description__icontains=q)
        ).select_related("department")
        results["departments"] = Department.objects.filter(is_active=True).filter(
            Q(title__icontains=q) | Q(summary__icontains=q)
        )
        results["products"] = Product.objects.available().filter(
            Q(name__icontains=q) | Q(generic_name__icontains=q) | Q(brand__icontains=q)
        )[:12]
    return render(request, "clinic/search.html", {"q": q, **results})

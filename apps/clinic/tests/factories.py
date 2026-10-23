import datetime

from apps.clinic.models import Department, Doctor, Specialty, WorkingHours


def make_department(slug="general-practice", kind=Department.Kind.CLINIC, **kwargs):
    defaults = {"title": f"بخش آزمایشی {slug}", "summary": "خلاصه"}
    defaults.update(kwargs)
    return Department.objects.create(slug=slug, kind=kind, **defaults)


def make_doctor(department=None, slug="dr-test", weekdays=range(7), start=datetime.time(9), end=datetime.time(10), slot=30, **kwargs):
    department = department or make_department()
    defaults = {
        "first_name": "سارا", "last_name": "احمدی", "medical_council_number": f"MC-{slug}",
        "specialty": None,
    }
    defaults.update(kwargs)
    doctor = Doctor.objects.create(slug=slug, department=department, **defaults)
    for weekday in weekdays:
        WorkingHours.objects.create(doctor=doctor, weekday=weekday, start_time=start, end_time=end, slot_minutes=slot)
    return doctor


def make_specialty(name="قلب", slug="cardio"):
    return Specialty.objects.create(name=name, slug=slug)

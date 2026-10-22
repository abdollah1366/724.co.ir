import datetime

from apps.clinic.models import Doctor, Floor, Specialty, WorkingHours


def make_floor(number=1, **kwargs):
    defaults = {"title": f"طبقه آزمایشی {number}", "slug": f"floor-{number}", "summary": "خلاصه"}
    defaults.update(kwargs)
    return Floor.objects.create(number=number, **defaults)


def make_doctor(floor=None, slug="dr-test", weekdays=range(7), start=datetime.time(9), end=datetime.time(10), slot=30, **kwargs):
    floor = floor or make_floor()
    defaults = {
        "first_name": "سارا", "last_name": "احمدی", "medical_council_number": f"MC-{slug}",
        "specialty": None,
    }
    defaults.update(kwargs)
    doctor = Doctor.objects.create(slug=slug, floor=floor, **defaults)
    for weekday in weekdays:
        WorkingHours.objects.create(doctor=doctor, weekday=weekday, start_time=start, end_time=end, slot_minutes=slot)
    return doctor


def make_specialty(name="قلب", slug="cardio"):
    return Specialty.objects.create(name=name, slug=slug)

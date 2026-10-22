import datetime

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.clinic.models import Doctor, Floor, Service, Specialty, WorkingHours

FLOORS = [
    {
        "number": 0, "slug": "pharmacy", "icon": "💊", "title": "داروخانه و فروشگاه سلامت",
        "summary": "داروهای نسخه‌ای و بدون نسخه، مشاوره دارویی، تجهیزات پزشکی و محصولات آرایشی و بهداشتی.",
        "services": [
            ("مشاوره دارویی با داروساز", False),
            ("پذیرش نسخه بیمه‌ای", False),
            ("تجهیزات پزشکی", False),
            ("محصولات آرایشی و بهداشتی", False),
        ],
    },
    {
        "number": 1, "slug": "general-practice", "icon": "🩺", "title": "پزشک عمومی و خدمات سرپایی",
        "summary": "ویزیت پزشک عمومی، پیگیری دیابت و فشارخون، پانسمان، بخیه و تزریقات.",
        "services": [
            ("ویزیت پزشک عمومی", False),
            ("پیگیری دیابت و فشارخون", False),
            ("پانسمان و بخیه", False),
            ("تزریقات و سونداژ", False),
            ("خدمات زیبایی (بوتاکس، فیلر، PRP، مزوتراپی)", True),
        ],
    },
    {
        "number": 2, "slug": "specialists", "icon": "👨‍⚕️", "title": "کلینیک پزشکان متخصص",
        "summary": "پزشکان متخصص با برنامه حضور مشخص و نوبت‌دهی اینترنتی.",
        "services": [],
    },
    {
        "number": 3, "slug": "laboratory", "icon": "🧪", "title": "آزمایشگاه تشخیص طبی",
        "summary": "آزمایش‌های تشخیصی و دوره‌ای، شرایط آمادگی و دریافت اینترنتی جواب (به‌زودی).",
        "services": [("آزمایش‌های روتین خون", False), ("آزمایش‌های چکاپ دوره‌ای", False)],
    },
    {
        "number": 4, "slug": "rehab", "icon": "🏃", "title": "خدمات توان‌بخشی و تخصصی",
        "summary": "فیزیوتراپی، توان‌بخشی، مشاوره تغذیه و سایر خدمات؛ پس از تعیین امکانات.",
        "coming_soon": True, "services": [],
    },
    {
        "number": 5, "slug": "cafe", "icon": "☕", "title": "کافی‌شاپ ۷۲۴",
        "summary": "نوشیدنی‌های گرم و سرد و میان‌وعده‌های سالم در فضایی آرام.",
        "services": [],
    },
]


class Command(BaseCommand):
    help = "ایجاد طبقات و خدمات پایه ساختمان ۷۲۴ (و در صورت نیاز پزشک نمونه برای آزمایش)."

    def add_arguments(self, parser):
        parser.add_argument("--demo-doctors", action="store_true", help="ایجاد پزشکان نمونه (فقط برای توسعه)")

    @transaction.atomic
    def handle(self, *args, **options):
        for data in FLOORS:
            floor, created = Floor.objects.update_or_create(
                number=data["number"],
                defaults={
                    "slug": data["slug"], "title": data["title"], "summary": data["summary"],
                    "icon": data["icon"], "is_coming_soon": data.get("coming_soon", False),
                },
            )
            for order, (title, qualified) in enumerate(data["services"]):
                Service.objects.get_or_create(
                    floor=floor, title=title,
                    defaults={"order": order, "requires_qualified_provider": qualified},
                )
            self.stdout.write(f"{'+' if created else '='} {floor}")

        if options["demo_doctors"]:
            self._create_demo_doctors()
        self.stdout.write(self.style.SUCCESS("انجام شد."))

    def _create_demo_doctors(self):
        gp_floor = Floor.objects.get(number=1)
        sp_floor = Floor.objects.get(number=2)
        cardio, _ = Specialty.objects.get_or_create(slug="cardiology", defaults={"name": "قلب و عروق"})
        demo = [
            ("نمونه", "پزشک‌عمومی", "demo-gp", None, gp_floor, "DEMO-0001"),
            ("نمونه", "متخصص‌قلب", "demo-cardio", cardio, sp_floor, "DEMO-0002"),
        ]
        for first, last, slug, specialty, floor, council in demo:
            doctor, created = Doctor.objects.get_or_create(
                slug=slug,
                defaults={
                    "first_name": first, "last_name": last, "specialty": specialty, "floor": floor,
                    "medical_council_number": council, "visit_fee": 2_500_000,
                    "bio": "این پزشک نمونه است و فقط برای آزمایش سامانه ساخته شده است.",
                },
            )
            if created:
                for weekday in (5, 6, 0, 1, 2):  # شنبه تا چهارشنبه
                    WorkingHours.objects.create(
                        doctor=doctor, weekday=weekday,
                        start_time=datetime.time(9, 0), end_time=datetime.time(13, 0), slot_minutes=20,
                    )
            self.stdout.write(f"{'+' if created else '='} {doctor}")

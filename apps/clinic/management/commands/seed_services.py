import datetime

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.clinic.models import Department, Doctor, Service, Specialty, WorkingHours
from apps.shop.models import Category, Product, ProductKind

K = Department.Kind

DEPARTMENTS = [
    ("pharmacy", K.PHARMACY, "💊", "داروخانه",
     "جست‌وجوی دارو، اطلاعات علمی، قیمت و موجودی، مشاوره با داروساز و پذیرش نسخه بیمه‌ای.",
     [("مشاوره دارویی با داروساز", False), ("پذیرش نسخه الکترونیک و بیمه‌ای", False)]),
    ("equipment", K.EQUIPMENT, "🩹", "تجهیزات پزشکی و محصولات بهداشتی",
     "فشارسنج، دستگاه تست قند، وسایل کمک‌حرکتی و محصولات آرایشی و بهداشتی.", []),
    ("general-practice", K.CLINIC, "🩺", "پزشک عمومی و خدمات سرپایی",
     "ویزیت پزشک عمومی، پیگیری دیابت و فشارخون، پانسمان، بخیه و تزریقات.",
     [("ویزیت پزشک عمومی", False), ("پیگیری دیابت و فشارخون", False), ("پانسمان و بخیه", False),
      ("تزریقات و سونداژ", False), ("خدمات زیبایی (بوتاکس، فیلر، PRP، مزوتراپی)", True)]),
    ("specialists", K.CLINIC, "👨‍⚕️", "پزشکان متخصص",
     "پزشکان متخصص با برنامه حضور مشخص و نوبت‌دهی اینترنتی.", []),
    ("laboratory", K.LAB, "🧪", "آزمایشگاه تشخیص طبی",
     "آزمایش‌های تشخیصی و دوره‌ای، شرایط آمادگی و دریافت اینترنتی جواب (به‌زودی).",
     [("آزمایش‌های روتین خون", False), ("آزمایش‌های چکاپ دوره‌ای", False)]),
    ("rehab", K.OTHER, "🏃", "توان‌بخشی و خدمات تخصصی",
     "فیزیوتراپی، توان‌بخشی، مشاوره تغذیه و سایر خدمات؛ پس از تعیین امکانات.", []),
    ("cafe", K.CAFE, "☕", "کافی‌شاپ ۷۲۴", "نوشیدنی‌های گرم و سرد و میان‌وعده‌های سالم در فضایی آرام.", []),
]

COMING_SOON = {"rehab"}

DEMO_PRODUCTS = [
    {
        "slug": "demo-acetaminophen-500", "kind": ProductKind.DRUG, "category": "painkillers",
        "name": "استامینوفن ۵۰۰ (نمونه)", "generic_name": "Acetaminophen (Paracetamol)", "dosage_form": "قرص",
        "strength": "۵۰۰ میلی‌گرم", "price": 450_000, "stock": 40,
        "short_description": "ضد درد و تب‌بر، بدون نیاز به نسخه",
        "mechanism": "مهار ساخت پروستاگلاندین‌ها عمدتاً در دستگاه عصبی مرکزی؛ اثر ضدالتهابی قابل توجهی ندارد.",
        "indications": "تب، سردرد، دندان‌درد، دردهای عضلانی و سایر دردهای خفیف تا متوسط.",
        "dosage_note": "طبق دستور پزشک یا داروساز و بروشور دارو مصرف شود. از حداکثر مقدار روزانه بروشور بیشتر مصرف نکنید.",
        "side_effects": "در مقادیر معمول عارضه کمی دارد؛ به‌ندرت واکنش حساسیتی پوستی.",
        "warnings": "مصرف بیش از حد می‌تواند به کبد آسیب جدی بزند. هم‌زمان با داروهای دیگری که استامینوفن دارند (مثل برخی داروهای سرماخوردگی) مصرف نکنید. بیماران کبدی و مصرف‌کنندگان الکل با پزشک مشورت کنند.",
        "interactions": "مصرف طولانی‌مدت با وارفارین ممکن است اثر ضدانعقادی را افزایش دهد. الکل خطر آسیب کبدی را بیشتر می‌کند.",
        "contraindications": "حساسیت به استامینوفن، بیماری شدید کبدی.",
        "pregnancy_note": "پیش از مصرف در بارداری و شیردهی با پزشک یا داروساز مشورت کنید.",
    },
    {
        "slug": "demo-amoxicillin-500", "kind": ProductKind.DRUG, "category": "antibiotics",
        "name": "آموکسی‌سیلین ۵۰۰ (نمونه)", "generic_name": "Amoxicillin", "dosage_form": "کپسول",
        "strength": "۵۰۰ میلی‌گرم", "price": 900_000, "stock": 25, "requires_prescription": True,
        "short_description": "آنتی‌بیوتیک خانواده پنی‌سیلین؛ فقط با نسخه پزشک",
        "mechanism": "مهار ساخت دیواره سلولی باکتری‌ها و در نتیجه از بین رفتن باکتری.",
        "indications": "عفونت‌های باکتریایی حساس مانند برخی عفونت‌های گوش، گلو، دستگاه تنفسی و ادراری، با تشخیص پزشک.",
        "dosage_note": "فقط طبق نسخه پزشک و تا پایان دوره درمان مصرف شود، حتی اگر علائم زودتر بهتر شد.",
        "side_effects": "اسهال، تهوع، بثورات پوستی. در صورت کهیر، تورم صورت یا تنگی نفس فوراً به اورژانس مراجعه کنید.",
        "warnings": "سرماخوردگی و آنفلوانزا (عفونت ویروسی) با آنتی‌بیوتیک درمان نمی‌شوند. مصرف خودسرانه باعث مقاومت میکروبی می‌شود.",
        "interactions": "ممکن است اثر وارفارین را تغییر دهد و دفع متوترکسات را کاهش دهد؛ پزشک را از همه داروهای مصرفی مطلع کنید.",
        "contraindications": "حساسیت به پنی‌سیلین‌ها یا سفالوسپورین‌ها.",
        "pregnancy_note": "در بارداری و شیردهی فقط با تجویز پزشک.",
    },
    {
        "slug": "demo-sunscreen-spf50", "kind": ProductKind.COSMETIC, "category": "skin-care",
        "name": "کرم ضدآفتاب SPF50 (نمونه)", "brand": "برند نمونه", "price": 3_500_000, "stock": 12,
        "short_description": "محافظت در برابر اشعه UVA و UVB، مناسب پوست‌های معمولی تا چرب",
    },
    {
        "slug": "demo-bp-monitor", "kind": ProductKind.EQUIPMENT, "category": "monitoring",
        "name": "فشارسنج دیجیتال بازویی (نمونه)", "brand": "برند نمونه", "price": 25_000_000, "stock": 5,
        "short_description": "اندازه‌گیری فشار خون و ضربان قلب در منزل",
        "specifications": "کاف بازویی ۲۲ تا ۳۲ سانتی‌متر\nحافظه ۶۰ اندازه‌گیری\nنمایش ضربان نامنظم\nباتری یا آداپتور",
        "warranty": "۱۸ ماه گارانتی شرکتی",
    },
    {
        "slug": "demo-glucometer", "kind": ProductKind.EQUIPMENT, "category": "monitoring",
        "name": "دستگاه تست قند خون (نمونه)", "brand": "برند نمونه", "price": 18_000_000, "stock": 0,
        "short_description": "پایش قند خون در منزل برای بیماران دیابتی",
        "specifications": "زمان اندازه‌گیری ۵ ثانیه\nحافظه ۵۰۰ نتیجه\nهمراه با ۱۰ نوار تست و قلم خون‌گیری",
        "warranty": "گارانتی مادام‌العمر دستگاه",
    },
]

CATEGORIES = [
    ("painkillers", "مسکن و تب‌بر", ProductKind.DRUG),
    ("antibiotics", "آنتی‌بیوتیک‌ها", ProductKind.DRUG),
    ("skin-care", "مراقبت پوست", ProductKind.COSMETIC),
    ("monitoring", "دستگاه‌های پایش سلامت", ProductKind.EQUIPMENT),
]


class Command(BaseCommand):
    help = "ایجاد بخش‌های خدماتی ۷۲۴ و در صورت نیاز داده نمونه (پزشک و محصول) برای آزمایش."

    def add_arguments(self, parser):
        parser.add_argument("--demo", action="store_true", help="ایجاد پزشکان و محصولات نمونه (فقط برای توسعه)")

    @transaction.atomic
    def handle(self, *args, **options):
        for order, (slug, kind, icon, title, summary, services) in enumerate(DEPARTMENTS):
            dept, created = Department.objects.update_or_create(
                slug=slug,
                defaults={
                    "kind": kind, "icon": icon, "title": title, "summary": summary,
                    "order": order * 10, "is_coming_soon": slug in COMING_SOON,
                },
            )
            for i, (service_title, qualified) in enumerate(services):
                Service.objects.get_or_create(
                    department=dept, title=service_title,
                    defaults={"order": i, "requires_qualified_provider": qualified},
                )
            self.stdout.write(f"{'+' if created else '='} {dept}")

        if options["demo"]:
            self._demo_doctors()
            self._demo_products()
        self.stdout.write(self.style.SUCCESS("انجام شد."))

    def _demo_doctors(self):
        cardio, _ = Specialty.objects.get_or_create(slug="cardiology", defaults={"name": "قلب و عروق"})
        demo = [
            ("پزشک‌عمومی", "demo-gp", None, "general-practice", "DEMO-0001"),
            ("متخصص‌قلب", "demo-cardio", cardio, "specialists", "DEMO-0002"),
        ]
        for last, slug, specialty, dept_slug, council in demo:
            doctor, created = Doctor.objects.get_or_create(
                slug=slug,
                defaults={
                    "first_name": "نمونه", "last_name": last, "specialty": specialty,
                    "department": Department.objects.get(slug=dept_slug), "medical_council_number": council,
                    "visit_fee": 2_500_000, "bio": "این پزشک نمونه است و فقط برای آزمایش سامانه ساخته شده است.",
                },
            )
            if created:
                for weekday in (5, 6, 0, 1, 2):  # شنبه تا چهارشنبه
                    WorkingHours.objects.create(
                        doctor=doctor, weekday=weekday,
                        start_time=datetime.time(9), end_time=datetime.time(13), slot_minutes=20,
                    )
            self.stdout.write(f"{'+' if created else '='} {doctor}")

    def _demo_products(self):
        categories = {}
        for order, (slug, name, kind) in enumerate(CATEGORIES):
            categories[slug], _ = Category.objects.get_or_create(slug=slug, defaults={"name": name, "kind": kind, "order": order})
        for data in DEMO_PRODUCTS:
            data = dict(data)
            slug = data.pop("slug")
            data["category"] = categories[data.pop("category")]
            product, created = Product.objects.get_or_create(slug=slug, defaults=data)
            self.stdout.write(f"{'+' if created else '='} {product}")

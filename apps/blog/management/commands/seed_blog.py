"""دسته‌های پیشنهادی وبلاگ؛ با --demo سه مقاله نمونه با گردش کار کامل (نوشتن، بازبینی، انتشار) هم ساخته می‌شود."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.audit.roles import setup_roles
from apps.blog.models import Category, Post, Tag
from apps.clinic.models import Department

CATEGORIES = [
    ("بیماری‌های مزمن", "chronic", "دیابت، فشار خون، چربی خون و پیگیری منظم آن‌ها", True),
    ("دارو و مصرف صحیح", "medicines", "نحوه مصرف، نگهداری و تداخل داروها", True),
    ("آزمایش و چک‌آپ", "lab-checkup", "آمادگی آزمایش، تفسیر عمومی و آزمایش‌های دوره‌ای", True),
    ("تغذیه و سبک زندگی", "nutrition", "تغذیه سالم، ورزش و خواب", True),
    ("پوست و زیبایی", "skin-beauty", "مراقبت از پوست و آگاهی درباره خدمات زیبایی مجاز", True),
    ("توانبخشی", "rehab", "فیزیوتراپی، کاردرمانی، گفتاردرمانی و شنوایی", True),
    ("سلامت روان", "mental-health", "استرس، خواب و زمان مراجعه به روانشناس", True),
    ("اخبار ۷۲۴", "news", "خبرها و رویدادهای مجموعه", False),
]

DEMO_POSTS = [
    {
        "title": "[نمونه] اندازه‌گیری درست فشار خون در خانه",
        "slug": "نمونه-اندازه-گیری-فشار-خون-در-خانه",
        "category": "chronic",
        "department_kind": Department.Kind.CLINIC,
        "tags": ["فشار خون"],
        "summary": "چند نکته ساده که عدد فشار خون خانگی را قابل اعتماد می‌کند. این متن نمونه است.",
        "body": (
            "این متن صرفاً نمونه‌ای برای نمایش قالب وبلاگ است و باید توسط پزشک بازنویسی شود.\n\n"
            "## پیش از اندازه‌گیری\n"
            "- نیم ساعت قبل چای، قهوه و سیگار مصرف نکنید.\n"
            "- پنج دقیقه آرام بنشینید.\n"
            "- مثانه خالی باشد.\n\n"
            "## هنگام اندازه‌گیری\n"
            "1. پشت به صندلی تکیه دهید و پاها روی زمین باشد.\n"
            "2. بازوبند هم‌سطح قلب باشد.\n"
            "3. دو بار با فاصله یک دقیقه اندازه بگیرید.\n\n"
            "## چه زمانی به پزشک مراجعه کنیم؟\n"
            "> اگر فشار خون شما بالا بود یا علائمی مانند سردرد شدید، درد قفسه سینه یا تنگی نفس دارید، بدون تأخیر با پزشک یا ۱۱۵ تماس بگیرید.\n\n"
            "برای پیگیری منظم می‌توانید [از پزشکان ۷۲۴ نوبت بگیرید](/doctors/)."
        ),
        "references": "منبع نمونه: راهنمای بالینی معتبر (پیش از انتشار با منبع واقعی جایگزین شود)",
    },
    {
        "title": "[نمونه] آمادگی برای آزمایش خون ناشتا",
        "slug": "نمونه-آمادگی-آزمایش-خون-ناشتا",
        "category": "lab-checkup",
        "department_kind": Department.Kind.LAB,
        "tags": ["آزمایش"],
        "summary": "ناشتا بودن یعنی چه و چه کارهایی را پیش از آزمایش انجام ندهیم. این متن نمونه است.",
        "body": (
            "این متن صرفاً نمونه است و باید توسط مسئول فنی آزمایشگاه بازنویسی شود.\n\n"
            "## ناشتا یعنی چه؟\n"
            "معمولاً یعنی چند ساعت پیش از نمونه‌گیری جز آب چیزی نخورید. مدت دقیق را **آزمایشگاه** برای هر آزمایش اعلام می‌کند.\n\n"
            "## داروها\n"
            "داروهای همیشگی را بدون هماهنگی با پزشک قطع نکنید."
        ),
        "references": "",
    },
    {
        "title": "[نمونه] ساعات کاری جدید ساختمان ۷۲۴",
        "slug": "نمونه-ساعات-کاری-جدید",
        "category": "news",
        "department_kind": None,
        "tags": [],
        "summary": "نمونه یک خبر مجموعه که نیاز به بازبینی علمی ندارد.",
        "body": "این یک خبر نمونه است.",
        "references": "",
    },
]


class Command(BaseCommand):
    help = "ساخت دسته‌های پیشنهادی وبلاگ (و با --demo مقاله‌های نمونه)"

    def add_arguments(self, parser):
        parser.add_argument("--demo", action="store_true", help="ساخت کاربران و مقاله‌های نمونه")

    @transaction.atomic
    def handle(self, *args, **options):
        for order, (name, slug, description, review) in enumerate(CATEGORIES):
            Category.objects.update_or_create(
                slug=slug, defaults={"name": name, "description": description, "requires_medical_review": review, "order": order}
            )
        self.stdout.write(f"{len(CATEGORIES)} دسته آماده است.")
        if options["demo"]:
            self._demo()

    def _user(self, username, first, last, role):
        user, created = get_user_model().objects.get_or_create(
            username=username, defaults={"first_name": first, "last_name": last, "is_staff": True}
        )
        if created:
            user.set_unusable_password()
            user.save()
        user.groups.add(Group.objects.get(name=role))
        return user

    def _demo(self):
        setup_roles()
        author = self._user("blog_author", "نویسنده", "نمونه", "نویسنده محتوا")
        reviewer = self._user("blog_reviewer", "دکتر", "بازبین نمونه", "بازبین علمی محتوا")
        self._user("blog_manager", "مدیر", "وبلاگ نمونه", "مدیر وبلاگ")
        for spec in DEMO_POSTS:
            if Post.objects.filter(slug=spec["slug"]).exists():
                continue
            post = Post.objects.create(
                title=spec["title"], slug=spec["slug"], category=Category.objects.get(slug=spec["category"]),
                summary=spec["summary"], body=spec["body"], references=spec["references"], author=author,
            )
            for name in spec["tags"]:
                tag, _ = Tag.objects.get_or_create(name=name, defaults={"slug": name.replace(" ", "-")})
                post.tags.add(tag)
            if spec["department_kind"]:
                post.departments.set(Department.objects.filter(kind=spec["department_kind"], is_active=True))
            post.submit()
            if post.status == Post.Status.IN_REVIEW:
                post.approve(reviewer, "نمونه؛ پیش از انتشار واقعی بازنویسی شود.")
            post.publish()
        self.stdout.write(self.style.SUCCESS(
            "مقاله‌های نمونه منتشر شد. کاربران نمونه blog_author، blog_reviewer و blog_manager رمز ندارند؛ برای ورود با changepassword رمز بدهید."
        ))

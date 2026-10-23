"""وبلاگ سلامت: مقاله‌ها با گردش کار نویسنده ← بازبینی علمی ← انتشار."""

import math

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone

# تغییر این فیلدها پس از تأیید علمی، مقاله را دوباره به بازبینی می‌فرستد.
REVIEWED_FIELDS = ("title", "summary", "body", "references")


class Category(models.Model):
    name = models.CharField("نام", max_length=80)
    slug = models.SlugField("نامک", unique=True, allow_unicode=True)
    description = models.CharField("توضیح کوتاه", max_length=300, blank=True)
    requires_medical_review = models.BooleanField(
        "نیاز به بازبینی علمی", default=True,
        help_text="برای دسته‌های غیرپزشکی (مثل اخبار مجموعه یا کافی‌شاپ) می‌توان غیرفعال کرد.",
    )
    order = models.PositiveSmallIntegerField("ترتیب نمایش", default=0)
    is_active = models.BooleanField("فعال", default=True)

    class Meta:
        ordering = ["order", "name"]
        verbose_name = "دسته مقاله"
        verbose_name_plural = "دسته‌های مقاله"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("blog:category", args=[self.slug])


class Tag(models.Model):
    name = models.CharField("نام", max_length=60, unique=True)
    slug = models.SlugField("نامک", unique=True, allow_unicode=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "برچسب"
        verbose_name_plural = "برچسب‌ها"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("blog:tag", args=[self.slug])


class PostQuerySet(models.QuerySet):
    def published(self):
        return self.filter(status=Post.Status.PUBLISHED, published_at__lte=timezone.now())


class Post(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "پیش‌نویس"
        IN_REVIEW = "in_review", "در انتظار بازبینی علمی"
        APPROVED = "approved", "تأیید علمی شده"
        PUBLISHED = "published", "منتشر شده"
        ARCHIVED = "archived", "بایگانی"

    title = models.CharField("عنوان", max_length=200)
    slug = models.SlugField("نامک", max_length=200, unique=True, allow_unicode=True)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="posts", verbose_name="دسته")
    tags = models.ManyToManyField(Tag, blank=True, related_name="posts", verbose_name="برچسب‌ها")
    departments = models.ManyToManyField(
        "clinic.Department", blank=True, related_name="posts", verbose_name="بخش‌های مرتبط",
        help_text="مقاله در صفحه این بخش‌ها نمایش داده می‌شود و خواننده به نوبت‌گیری هدایت می‌شود.",
    )
    summary = models.CharField("خلاصه", max_length=400, help_text="در فهرست مقاله‌ها و نتایج جست‌وجو نمایش داده می‌شود.")
    body = models.TextField(
        "متن",
        help_text="پاراگراف‌ها را با یک خط خالی جدا کنید. «## » و «### » عنوان، «- » فهرست، «۱. » یا «1. » فهرست شماره‌دار، "
                  "«> » نکته مهم، **متن** پررنگ و [متن](https://...) پیوند است.",
    )
    references = models.TextField("منابع علمی", blank=True, help_text="هر منبع در یک خط.")
    cover = models.ImageField("تصویر شاخص", upload_to="blog/%Y/%m/", blank=True)
    cover_alt = models.CharField("متن جایگزین تصویر", max_length=200, blank=True)
    meta_title = models.CharField("عنوان سئو", max_length=70, blank=True, help_text="خالی = عنوان مقاله")
    meta_description = models.CharField("توضیح سئو", max_length=160, blank=True, help_text="خالی = خلاصه مقاله")
    is_featured = models.BooleanField("مقاله ویژه", default=False)

    status = models.CharField("وضعیت", max_length=12, choices=Status.choices, default=Status.DRAFT, db_index=True)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="blog_posts", verbose_name="نویسنده"
    )
    author_display = models.CharField(
        "نام نمایشی نویسنده", max_length=120, blank=True, help_text="مثلاً «دکتر ...، متخصص داخلی». خالی = نام کاربر"
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+",
        verbose_name="بازبین علمی",
    )
    reviewer_display = models.CharField("نام نمایشی بازبین", max_length=120, blank=True)
    reviewed_at = models.DateTimeField("زمان بازبینی", null=True, blank=True)
    review_note = models.TextField("یادداشت بازبین", blank=True)
    published_at = models.DateTimeField(
        "زمان انتشار", null=True, blank=True, db_index=True, help_text="زمان آینده = انتشار زمان‌بندی‌شده"
    )
    created_at = models.DateTimeField("ایجاد", auto_now_add=True)
    updated_at = models.DateTimeField("آخرین ویرایش", auto_now=True)

    objects = PostQuerySet.as_manager()

    class Meta:
        ordering = ["-published_at", "-created_at"]
        verbose_name = "مقاله"
        verbose_name_plural = "مقاله‌ها"
        permissions = [
            ("review_post", "بازبینی علمی و تأیید مقاله"),
            ("publish_post", "انتشار و بایگانی مقاله"),
        ]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("blog:detail", args=[self.slug])

    @property
    def is_live(self):
        return self.status == self.Status.PUBLISHED and self.published_at is not None and self.published_at <= timezone.now()

    @property
    def needs_review(self):
        return self.category.requires_medical_review

    @property
    def reading_minutes(self):
        return max(1, math.ceil(len(self.body.split()) / 200))

    @property
    def author_name(self):
        return self.author_display or self.author.get_full_name() or self.author.get_username()

    @property
    def reviewer_name(self):
        if self.reviewer is None:
            return ""
        return self.reviewer_display or self.reviewer.get_full_name() or self.reviewer.get_username()

    # ---- گردش کار ----

    def submit(self):
        if self.status not in (self.Status.DRAFT,):
            raise ValidationError("فقط پیش‌نویس را می‌توان برای بازبینی فرستاد.")
        self.status = self.Status.IN_REVIEW if self.needs_review else self.Status.APPROVED
        self.save()

    def approve(self, user, note=""):
        if self.status != self.Status.IN_REVIEW:
            raise ValidationError("این مقاله در انتظار بازبینی نیست.")
        if user.pk == self.author_id:
            raise ValidationError("نویسنده نمی‌تواند مقاله خودش را بازبینی علمی کند.")
        self.status = self.Status.APPROVED
        self.reviewer = user
        self.reviewed_at = timezone.now()
        self.review_note = note or self.review_note
        self.save()

    def reject(self, user, note):
        if self.status != self.Status.IN_REVIEW:
            raise ValidationError("این مقاله در انتظار بازبینی نیست.")
        if not note:
            raise ValidationError("علت برگشت را بنویسید تا نویسنده بتواند اصلاح کند.")
        self.status = self.Status.DRAFT
        self.review_note = note
        self.save()

    def publish(self, when=None):
        if self.status not in (self.Status.APPROVED, self.Status.ARCHIVED):
            raise ValidationError("مقاله پیش از انتشار باید تأیید علمی شود.")
        if self.needs_review and self.reviewer_id is None:
            raise ValidationError("این دسته نیاز به بازبینی علمی دارد و مقاله بازبین ندارد.")
        self.status = self.Status.PUBLISHED
        self.published_at = when or self.published_at or timezone.now()
        self.save()

    def archive(self):
        if self.status != self.Status.PUBLISHED:
            raise ValidationError("فقط مقاله منتشرشده بایگانی می‌شود.")
        self.status = self.Status.ARCHIVED
        self.save()

    def save(self, *args, **kwargs):
        # تغییر محتوای علمی پس از تأیید، تأیید را باطل می‌کند و مقاله از سایت برداشته می‌شود تا دوباره بازبینی شود.
        if self.pk and self.status in (self.Status.APPROVED, self.Status.PUBLISHED) and self.needs_review:
            old = Post.objects.filter(pk=self.pk).values(*REVIEWED_FIELDS).first()
            if old and any(old[f] != getattr(self, f) for f in REVIEWED_FIELDS):
                self.status = self.Status.IN_REVIEW
                self.reviewer = None
                self.reviewed_at = None
        super().save(*args, **kwargs)

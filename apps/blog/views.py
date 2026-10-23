import json

from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils.safestring import mark_safe

from . import markup
from .models import Category, Post, Tag

PAGE_SIZE = 9


def _listing(request, posts, **context):
    query = request.GET.get("q", "").strip()
    if query:
        posts = posts.filter(Q(title__icontains=query) | Q(summary__icontains=query) | Q(body__icontains=query))
    page = Paginator(posts.select_related("category"), PAGE_SIZE).get_page(request.GET.get("page"))
    context.update({
        "page": page,
        "query": query,
        "categories": Category.objects.filter(is_active=True),
    })
    return render(request, "blog/list.html", context)


def post_list(request):
    posts = Post.objects.published()
    featured = None
    if not request.GET.get("q") and not request.GET.get("page"):
        featured = posts.filter(is_featured=True).select_related("category").first()
        if featured:
            posts = posts.exclude(pk=featured.pk)
    return _listing(request, posts, featured=featured)


def category(request, slug):
    cat = get_object_or_404(Category, slug=slug, is_active=True)
    return _listing(request, Post.objects.published().filter(category=cat), current_category=cat)


def tag(request, slug):
    tag_obj = get_object_or_404(Tag, slug=slug)
    return _listing(request, Post.objects.published().filter(tags=tag_obj), current_tag=tag_obj)


def _structured_data(post):
    """داده ساختاریافته schema.org برای گوگل: مقاله پزشکی با نویسنده و بازبین."""
    data = {
        "@context": "https://schema.org",
        "@type": "MedicalWebPage" if post.needs_review else "Article",
        "headline": post.title,
        "description": post.meta_description or post.summary,
        "inLanguage": "fa-IR",
        "url": settings.SITE_URL + post.get_absolute_url(),
        "author": {"@type": "Person", "name": post.author_name},
        "publisher": {"@type": "MedicalOrganization", "name": settings.SITE_NAME, "url": settings.SITE_URL},
        "dateModified": post.updated_at.isoformat(),
    }
    if post.published_at:
        data["datePublished"] = post.published_at.isoformat()
    if post.reviewer_id:
        data["reviewedBy"] = {"@type": "Person", "name": post.reviewer_name}
        data["lastReviewed"] = post.reviewed_at.date().isoformat()
    if post.cover:
        data["image"] = settings.SITE_URL + post.cover.url
    text = json.dumps(data, ensure_ascii=False)
    # جلوگیری از بسته شدن تگ script با محتوای مقاله
    return mark_safe(text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def detail(request, slug):
    post = get_object_or_404(Post.objects.select_related("category", "author", "reviewer"), slug=slug)
    preview = not post.is_live
    # پیش‌نویس‌ها فقط برای کارکنانی که مجوز دیدن مقاله را دارند (پیش‌نمایش) نمایش داده می‌شوند.
    if preview and not (request.user.is_staff and request.user.has_perm("blog.view_post")):
        raise Http404
    related = Post.objects.published().filter(category=post.category).exclude(pk=post.pk)[:3]
    return render(request, "blog/detail.html", {
        "post": post,
        "body_html": markup.render(post.body),
        "toc": markup.headings(post.body),
        "references": [line.strip() for line in post.references.splitlines() if line.strip()],
        "related": related,
        "departments": post.departments.filter(is_active=True),
        "preview": preview,
        "structured_data": _structured_data(post),
    })

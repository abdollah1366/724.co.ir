from django.conf import settings
from django.contrib.syndication.views import Feed
from django.urls import reverse_lazy

from .models import Post


class LatestPostsFeed(Feed):
    title = f"مقالات سلامت {settings.SITE_NAME}"
    link = reverse_lazy("blog:list")
    description = "تازه‌ترین مقاله‌های آموزشی سلامت، بازبینی‌شده توسط پزشکان"

    def items(self):
        return Post.objects.published()[:20]

    def item_title(self, item):
        return item.title

    def item_description(self, item):
        return item.summary

    def item_pubdate(self, item):
        return item.published_at

    def item_updateddate(self, item):
        return item.updated_at

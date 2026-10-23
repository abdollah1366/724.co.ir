from django.contrib.sitemaps import Sitemap

from .models import Category, Post


class PostSitemap(Sitemap):
    changefreq = "monthly"
    priority = 0.7

    def items(self):
        return Post.objects.published()

    def lastmod(self, item):
        return item.updated_at


class CategorySitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.5

    def items(self):
        return Category.objects.filter(is_active=True)

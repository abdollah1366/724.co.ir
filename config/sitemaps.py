"""نقشه سایت برای موتورهای جست‌وجو: فقط صفحات عمومی؛ هیچ صفحه بیمار، پرونده یا پنل کارکنان در آن نیست."""

from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from apps.blog.sitemaps import CategorySitemap, PostSitemap
from apps.clinic.models import Department


class StaticSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.8

    def items(self):
        return ["core:home", "core:about", "core:contact", "clinic:department_list", "clinic:doctor_list",
                "lab:home", "shop:pharmacy", "shop:equipment", "blog:list"]

    def location(self, item):
        return reverse(item)


class DepartmentSitemap(Sitemap):
    changefreq = "monthly"
    priority = 0.6

    def items(self):
        return Department.objects.filter(is_active=True)


SITEMAPS = {
    "pages": StaticSitemap,
    "departments": DepartmentSitemap,
    "blog": PostSitemap,
    "blog-categories": CategorySitemap,
}

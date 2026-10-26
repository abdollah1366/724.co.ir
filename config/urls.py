import os
import re

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path, re_path
from django.views.generic import TemplateView

from apps.accounts import views as accounts_views

from .sitemaps import SITEMAPS

# آدرس پنل مدیریت را در محیط واقعی با DJANGO_ADMIN_URL تغییر دهید.
ADMIN_URL = os.environ.get("DJANGO_ADMIN_URL", "admin/")

urlpatterns = [
    path(ADMIN_URL, admin.site.urls),
    path("appointments/", include("apps.appointments.urls")),
    path("accounts/", include("apps.accounts.urls")),
    path("club/", accounts_views.club, name="club"),
    path("backoffice/", include("apps.backoffice.urls")),
    path("lab/", include("apps.lab.urls")),
    path("ehr/", include("apps.ehr.urls")),
    path("blog/", include("apps.blog.urls")),
    path("consult/", include("apps.telemed.urls")),
    path("sitemap.xml", sitemap, {"sitemaps": SITEMAPS}, name="sitemap"),
    path("robots.txt", TemplateView.as_view(template_name="robots.txt", content_type="text/plain")),
    path("", include("apps.shop.urls")),
    path("", include("apps.clinic.urls")),
    path("", include("apps.core.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
elif settings.SERVE_MEDIA:
    # هاست اشتراکی: فقط تصاویر عمومی؛ مدارک بیماران در PRIVATE_MEDIA_ROOT است و اینجا نیست.
    from apps.core.views import public_media

    urlpatterns += [re_path(r"^%s(?P<path>.*)$" % re.escape(settings.MEDIA_URL.lstrip("/")), public_media)]

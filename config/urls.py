import os

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.accounts import views as accounts_views

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
    path("", include("apps.shop.urls")),
    path("", include("apps.clinic.urls")),
    path("", include("apps.core.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

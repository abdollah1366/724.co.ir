from django.conf import settings


def site_info(request):
    return {
        "SITE_NAME": settings.SITE_NAME,
        "SITE_TAGLINE": settings.SITE_TAGLINE,
        "SITE_PHONE": settings.SITE_PHONE,
        "SITE_ADDRESS": settings.SITE_ADDRESS,
        "SITE_URL": settings.SITE_URL,
        "LAB_INFO": settings.LAB_INFO,
    }

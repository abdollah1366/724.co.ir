from django.apps import AppConfig


class BlogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.blog"
    verbose_name = "وبلاگ و مقالات سلامت"

    def ready(self):
        from apps.audit import log

        from .models import Post

        log.track(Post)

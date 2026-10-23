from django.apps import AppConfig


class EhrConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.ehr"
    verbose_name = "پرونده الکترونیک سلامت"

    def ready(self):
        from . import signals  # noqa: F401

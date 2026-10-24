from django.apps import AppConfig


class LabConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.lab"
    verbose_name = "آزمایشگاه تشخیص طبی"

    def ready(self):
        from . import signals  # noqa: F401

from django.apps import AppConfig


class AuditConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.audit"
    verbose_name = "کنترل، نقش‌ها و گزارش تغییرات"

    def ready(self):
        from django.db.models.signals import post_migrate

        from . import signals  # noqa: F401
        from .roles import setup_roles_after_migrate

        post_migrate.connect(setup_roles_after_migrate, dispatch_uid="audit-setup-roles")

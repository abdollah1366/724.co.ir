from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    verbose_name = "کاربران و باشگاه مشتریان"

    def ready(self):
        from . import signals  # noqa: F401

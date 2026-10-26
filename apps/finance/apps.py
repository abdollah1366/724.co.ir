from django.apps import AppConfig


class FinanceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.finance"
    verbose_name = "مالی (صورتحساب، قرارداد، تسویه، حقوق)"

    def ready(self):
        from django.apps import apps as registry

        from apps.audit import log

        from . import flags, integration  # noqa: F401 - ثبت قابلیت‌ها و handlerهای رویداد

        # ردیف‌های محاسبه‌شده حجیم‌اند و از روی سند اصلی قابل بازسازی؛ بقیه در گزارش تغییرات ثبت می‌شوند.
        skip = {"SettlementLine", "PayrollItem"}
        for model in registry.get_app_config(self.label).get_models():
            if model.__name__ not in skip:
                log.track(model)
        from apps.core.models import FeatureFlag

        log.track(FeatureFlag)

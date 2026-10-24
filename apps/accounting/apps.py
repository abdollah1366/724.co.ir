from django.apps import AppConfig


class AccountingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounting"
    verbose_name = "حسابداری"

    def ready(self):
        from django.apps import apps as registry

        from apps.audit import log

        # همه مدل‌ها به‌جز ردیف‌های دفتر که خودشان تغییرناپذیرند
        for model in registry.get_app_config(self.label).get_models():
            if model.__name__ not in ("JournalLine", "JournalEntry", "SalesAllocation"):
                log.track(model)

from django.apps import AppConfig


class SalesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.sales"
    verbose_name = "فروش و صندوق"

    def ready(self):
        from django.apps import apps as registry

        from apps.audit import log

        # همه مدل‌ها به‌جز ردیف‌های دفتر که خودشان تغییرناپذیرند
        for model in registry.get_app_config(self.label).get_models():
            if model.__name__ not in ("JournalLine", "JournalEntry", "SalesAllocation"):
                log.track(model)

from django.apps import AppConfig


class TelemedConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.telemed"
    verbose_name = "مشاوره و ویزیت آنلاین"

    def ready(self):
        from apps.audit import log

        from . import checks  # noqa: F401
        from .models import ConsultPayment, DoctorOnlineProfile

        # فقط داده‌های غیربالینی؛ متن گفتگو و موضوع مشاوره عمداً در گزارش تغییرات (که مدیر کل می‌بیند) نمی‌آید.
        log.track(ConsultPayment)
        log.track(DoctorOnlineProfile)

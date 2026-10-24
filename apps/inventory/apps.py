from django.apps import AppConfig


class InventoryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.inventory"
    verbose_name = "انبار و موجودی"

    def ready(self):
        from apps.audit import log
        from apps.shop.models import Order, Product, ProductPackage

        from . import models

        for model in (Product, ProductPackage, Order, models.Warehouse, models.Location, models.Batch,
                      models.InventoryDocument, models.InventoryDocumentLine):
            log.track(model)

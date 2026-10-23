from .models import Product

SESSION_KEY = "cart"
MAX_QUANTITY = 10


class Cart:
    """سبد خرید ساده مبتنی بر نشست. فقط محصولات قابل سفارش آنلاین پذیرفته می‌شوند."""

    def __init__(self, request):
        self.session = request.session
        self.data = {str(k): int(v) for k, v in self.session.get(SESSION_KEY, {}).items()}

    def _save(self):
        self.session[SESSION_KEY] = self.data
        self.session.modified = True

    def set(self, product, quantity):
        quantity = max(0, min(int(quantity), MAX_QUANTITY, product.stock))
        if quantity == 0 or not product.can_order_online:
            self.data.pop(str(product.pk), None)
        else:
            self.data[str(product.pk)] = quantity
        self._save()

    def add(self, product, quantity=1):
        self.set(product, self.data.get(str(product.pk), 0) + int(quantity))

    def remove(self, product):
        self.data.pop(str(product.pk), None)
        self._save()

    def clear(self):
        self.data = {}
        self._save()

    def lines(self):
        products = Product.objects.available().in_bulk([int(pk) for pk in self.data])
        result = []
        for pk, qty in self.data.items():
            product = products.get(int(pk))
            if product and product.can_order_online:
                result.append({"product": product, "quantity": qty, "total": product.price * qty})
        return result

    @property
    def count(self):
        return sum(self.data.values())

    def total(self):
        return sum(line["total"] for line in self.lines())

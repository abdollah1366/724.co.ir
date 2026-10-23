from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.shop.models import Order, PharmacyQuestion, Product


def make_product(slug="paracetamol", **kwargs):
    defaults = {"kind": Product.Kind.DRUG, "name": f"محصول {slug}", "price": 500_000, "stock": 5}
    defaults.update(kwargs)
    return Product.objects.create(slug=slug, **defaults)


class CatalogTests(TestCase):
    def setUp(self):
        self.otc = make_product(generic_name="Acetaminophen", indications="تب و درد", warnings="آسیب کبدی", interactions="وارفارین")
        self.rx = make_product(slug="amoxicillin", requires_prescription=True)
        self.device = make_product(slug="bp-monitor", kind=Product.Kind.EQUIPMENT, specifications="حافظه ۶۰")

    def test_pharmacy_lists_only_drugs(self):
        response = self.client.get(reverse("shop:pharmacy"))
        self.assertContains(response, self.otc.name)
        self.assertNotContains(response, self.device.name)

    def test_equipment_page(self):
        response = self.client.get(reverse("shop:equipment"))
        self.assertContains(response, self.device.name)

    def test_search_by_generic_name(self):
        response = self.client.get(reverse("shop:pharmacy"), {"q": "acetaminophen"})
        self.assertContains(response, self.otc.name)
        self.assertNotContains(response, self.rx.name)

    def test_in_stock_filter(self):
        make_product(slug="empty", stock=0, name="داروی تمام‌شده")
        response = self.client.get(reverse("shop:pharmacy"), {"in_stock": "1"})
        self.assertNotContains(response, "داروی تمام‌شده")

    def test_drug_detail_shows_scientific_info(self):
        response = self.client.get(self.otc.get_absolute_url())
        for text in ["موارد مصرف", "آسیب کبدی", "تداخلات دارویی", "وارفارین", "افزودن به سبد خرید"]:
            self.assertContains(response, text)

    def test_prescription_drug_cannot_be_added(self):
        response = self.client.get(self.rx.get_absolute_url())
        self.assertContains(response, "نیازمند نسخه پزشک")
        self.assertNotContains(response, "افزودن به سبد خرید")
        self.client.post(reverse("shop:cart_add", args=[self.rx.pk]))
        self.assertEqual(self.client.session.get("cart", {}), {})

    def test_inactive_product_hidden(self):
        self.otc.is_active = False
        self.otc.save()
        self.assertEqual(self.client.get(self.otc.get_absolute_url()).status_code, 404)


class CartAndCheckoutTests(TestCase):
    def setUp(self):
        self.product = make_product(stock=3)
        self.user = get_user_model().objects.create_user("09121234567", password="x-strong-pass-1")

    def checkout_data(self, **overrides):
        data = {"full_name": "مریم رضایی", "mobile": "09121234567", "delivery_method": "pickup", "consent": "on"}
        data.update(overrides)
        return data

    def test_cart_quantity_capped_by_stock(self):
        self.client.post(reverse("shop:cart_add", args=[self.product.pk]), {"quantity": 9})
        self.assertEqual(self.client.session["cart"], {str(self.product.pk): 3})

    def test_cart_update_to_zero_removes(self):
        self.client.post(reverse("shop:cart_add", args=[self.product.pk]))
        self.client.post(reverse("shop:cart_update", args=[self.product.pk]), {"quantity": 0})
        self.assertEqual(self.client.session["cart"], {})

    def test_checkout_requires_login(self):
        self.client.post(reverse("shop:cart_add", args=[self.product.pk]))
        response = self.client.get(reverse("shop:checkout"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_checkout_creates_order_and_reduces_stock(self):
        self.client.force_login(self.user)
        self.client.post(reverse("shop:cart_add", args=[self.product.pk]), {"quantity": 2})
        response = self.client.post(reverse("shop:checkout"), self.checkout_data())
        order = Order.objects.get()
        self.assertRedirects(response, reverse("shop:order_detail", args=[order.code]))
        self.assertEqual(order.total, 1_000_000)
        self.assertEqual(order.items.get().quantity, 2)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 1)
        self.assertEqual(self.client.session["cart"], {})

    def test_courier_requires_address(self):
        self.client.force_login(self.user)
        self.client.post(reverse("shop:cart_add", args=[self.product.pk]))
        self.client.post(reverse("shop:checkout"), self.checkout_data(delivery_method="courier"))
        self.assertFalse(Order.objects.exists())

    def test_out_of_stock_at_checkout_rolls_back(self):
        self.client.force_login(self.user)
        self.client.post(reverse("shop:cart_add", args=[self.product.pk]), {"quantity": 2})
        Product.objects.filter(pk=self.product.pk).update(stock=1)
        self.client.post(reverse("shop:checkout"), self.checkout_data())
        self.assertFalse(Order.objects.exists())
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 1)

    def test_cancel_restores_stock_and_other_users_cannot_see(self):
        self.client.force_login(self.user)
        self.client.post(reverse("shop:cart_add", args=[self.product.pk]), {"quantity": 2})
        self.client.post(reverse("shop:checkout"), self.checkout_data())
        order = Order.objects.get()
        self.client.post(reverse("shop:order_cancel", args=[order.code]))
        order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CANCELLED)
        self.assertEqual(self.product.stock, 3)

        other = get_user_model().objects.create_user("09350000000", password="x-strong-pass-2")
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse("shop:order_detail", args=[order.code])).status_code, 404)


class ConsultTests(TestCase):
    def test_question_saved(self):
        product = make_product()
        self.client.post(
            reverse("shop:consult"),
            {"name": "علی", "mobile": "09121234567", "question": "با فشارخون مصرف کنم؟", "product": product.slug},
        )
        question = PharmacyQuestion.objects.get()
        self.assertEqual(question.product, product)

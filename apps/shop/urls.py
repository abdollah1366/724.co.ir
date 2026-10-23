from django.urls import path

from . import views

app_name = "shop"

urlpatterns = [
    path("pharmacy/", views.product_list, {"kind": "drug"}, name="pharmacy"),
    path("equipment/", views.product_list, {"kind": "equipment"}, name="equipment"),
    path("cosmetics/", views.product_list, {"kind": "cosmetic"}, name="cosmetics"),
    path("products/<str:slug>/", views.product_detail, name="product_detail"),
    path("cart/", views.cart_detail, name="cart"),
    path("cart/add/<int:pk>/", views.cart_add, name="cart_add"),
    path("cart/update/<int:pk>/", views.cart_update, name="cart_update"),
    path("checkout/", views.checkout, name="checkout"),
    path("orders/<str:code>/", views.order_detail, name="order_detail"),
    path("orders/<str:code>/cancel/", views.order_cancel, name="order_cancel"),
    path("pharmacy/consult/", views.consult, name="consult"),
]

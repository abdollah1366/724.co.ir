from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import F, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .cart import Cart
from .forms import CheckoutForm, PharmacyQuestionForm
from .models import Category, Order, OrderItem, Product, ProductKind

KIND_PAGES = {
    "drug": ("داروخانه آنلاین", "جست‌وجوی دارو، اطلاعات علمی، قیمت و موجودی"),
    "equipment": ("تجهیزات پزشکی", "فشارسنج، دستگاه تست قند، وسایل کمک‌حرکتی و سایر تجهیزات"),
    "cosmetic": ("آرایشی و بهداشتی", "محصولات مراقبت پوست، مو و بهداشت شخصی"),
}


class OutOfStock(Exception):
    pass


def product_list(request, kind):
    title, subtitle = KIND_PAGES[kind]
    products = Product.objects.available().filter(kind=kind).select_related("category")
    q = request.GET.get("q", "").strip()[:100]
    if q:
        products = products.filter(
            Q(name__icontains=q) | Q(generic_name__icontains=q) | Q(brand__icontains=q) | Q(indications__icontains=q)
        )
    category = request.GET.get("category")
    if category:
        products = products.filter(category__slug=category)
    if request.GET.get("in_stock"):
        products = products.filter(stock__gt=0)
    return render(
        request,
        "shop/product_list.html",
        {
            "kind": kind,
            "title": title,
            "subtitle": subtitle,
            "products": products,
            "categories": Category.objects.filter(kind=kind),
            "current_category": category,
            "q": q,
        },
    )


def product_detail(request, slug):
    product = get_object_or_404(Product.objects.available().select_related("category"), slug=slug)
    related = Product.objects.available().filter(kind=product.kind).exclude(pk=product.pk)
    if product.category_id:
        related = related.filter(category_id=product.category_id)
    return render(request, "shop/product_detail.html", {"product": product, "related": related[:4]})


def cart_detail(request):
    cart = Cart(request)
    return render(request, "shop/cart.html", {"lines": cart.lines(), "total": cart.total()})


@require_POST
def cart_add(request, pk):
    product = get_object_or_404(Product.objects.available(), pk=pk)
    if not product.can_order_online:
        messages.error(request, "این محصول قابل سفارش آنلاین نیست.")
        return redirect(product)
    try:
        quantity = int(request.POST.get("quantity", 1))
    except ValueError:
        quantity = 1
    Cart(request).add(product, max(quantity, 1))
    messages.success(request, f"«{product.name}» به سبد خرید اضافه شد.")
    return redirect("shop:cart")


@require_POST
def cart_update(request, pk):
    product = get_object_or_404(Product, pk=pk)
    try:
        quantity = int(request.POST.get("quantity", 0))
    except ValueError:
        quantity = 0
    Cart(request).set(product, quantity)
    return redirect("shop:cart")


@login_required
def checkout(request):
    cart = Cart(request)
    lines = cart.lines()
    if not lines:
        messages.info(request, "سبد خرید شما خالی است.")
        return redirect("shop:cart")

    profile = getattr(request.user, "profile", None)
    initial = {"full_name": request.user.get_full_name(), "mobile": profile.mobile if profile else ""}
    form = CheckoutForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            order = _create_order(request.user, form, lines)
        except OutOfStock as exc:
            messages.error(request, f"موجودی «{exc}» کافی نیست. لطفاً تعداد را تغییر دهید.")
            return redirect("shop:cart")
        cart.clear()
        messages.success(request, "سفارش شما ثبت شد. پس از بررسی داروساز با شما تماس گرفته می‌شود.")
        return redirect("shop:order_detail", code=order.code)
    return render(request, "shop/checkout.html", {"form": form, "lines": lines, "total": cart.total()})


@transaction.atomic
def _create_order(user, form, lines):
    order = form.save(commit=False)
    order.user = user
    order.total = sum(line["total"] for line in lines)
    order.save()
    for line in lines:
        product, qty = line["product"], line["quantity"]
        # کم کردن موجودی فقط اگر کافی باشد (ایمن در برابر سفارش هم‌زمان)
        updated = Product.objects.filter(pk=product.pk, stock__gte=qty).update(stock=F("stock") - qty)
        if not updated:
            raise OutOfStock(product.name)
        OrderItem.objects.create(
            order=order, product=product, product_name=product.name, unit_price=product.price, quantity=qty
        )
    return order


@login_required
def order_detail(request, code):
    order = get_object_or_404(Order.objects.prefetch_related("items"), code=code, user=request.user)
    return render(request, "shop/order_detail.html", {"order": order})


@login_required
@require_POST
def order_cancel(request, code):
    order = get_object_or_404(Order, code=code, user=request.user)
    if order.status == Order.Status.PENDING and order.cancel():
        messages.success(request, "سفارش لغو شد.")
    else:
        messages.error(request, "این سفارش دیگر قابل لغو نیست.")
    return redirect("shop:order_detail", code=order.code)


def consult(request):
    product = None
    slug = request.GET.get("product") or request.POST.get("product")
    if slug:
        product = Product.objects.available().filter(slug=slug).first()
    initial = {}
    if request.user.is_authenticated:
        profile = getattr(request.user, "profile", None)
        initial = {"name": request.user.get_full_name(), "mobile": profile.mobile if profile else ""}
        if product:
            initial["question"] = f"درباره «{product.name}»: "
    form = PharmacyQuestionForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        question = form.save(commit=False)
        question.product = product
        if request.user.is_authenticated:
            question.user = request.user
        question.save()
        messages.success(request, "سؤال شما ثبت شد. داروساز به‌زودی پاسخ می‌دهد.")
        return redirect("shop:consult")
    return render(request, "shop/consult.html", {"form": form, "product": product})

"""صندوق‌های مجموعه: فهرست و وضعیت برای مدیر سیستم، و باز/بستن شیفت برای صندوق‌دار هر بخش."""

from django import forms
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.sales import cashbox
from apps.sales import services as sales
from apps.sales.models import CashRegister, CashSession

# هر کس یکی از این مجوزها را دارد می‌تواند در صندوق بخش خودش شیفت باز کند.
CASHIER_PERMS = ("sales.use_pos", "finance.use_cashier", "lab.post_labpayment")
MANAGE_PERMS = ("sales.view_cashregister", "sales.change_cashregister")


def _any(user, perms):
    return user.is_superuser or any(user.has_perm(p) for p in perms)


class OpenForm(forms.Form):
    register = forms.ModelChoiceField(label="صندوق", queryset=CashRegister.objects.none())
    opening_cash = forms.IntegerField(label="موجودی اولیه نقد (ریال)", min_value=0, initial=0)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        allowed = [r.pk for r in cashbox.registers_for(user) if r.department != CashRegister.Department.PHARMACY
                   or user.has_perm("sales.use_pos")]
        self.fields["register"].queryset = CashRegister.objects.filter(pk__in=allowed)


class CloseForm(forms.Form):
    counted_cash = forms.IntegerField(label="نقد شمارش‌شده (ریال)", min_value=0)


@staff_member_required
def cashboxes(request):
    if not _any(request.user, MANAGE_PERMS):
        raise PermissionDenied
    return render(request, "backoffice/cashboxes.html", {
        "rows": cashbox.status_rows(), "departments": CashRegister.Department.choices,
        "can_edit": request.user.has_perm("sales.change_cashregister"),
        "can_add": request.user.has_perm("sales.add_cashregister"),
        "today": cashbox.today_sessions(),
    })


@staff_member_required
def my_cashbox(request):
    if not _any(request.user, CASHIER_PERMS):
        raise PermissionDenied
    session = cashbox.user_session(request.user)
    if request.method == "POST" and session is None:
        form = OpenForm(request.POST, user=request.user)
        if form.is_valid():
            try:
                sales.open_session(form.cleaned_data["register"], request.user, form.cleaned_data["opening_cash"])
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
            except IntegrityError:
                messages.error(request, "این صندوق شیفت باز دارد.")
            else:
                messages.success(request, "شیفت صندوق باز شد.")
        return redirect("backoffice:my_cashbox")
    context = {"session": session}
    if session:
        context.update({"expected": sales.session_expected_cash(session), "close_form": CloseForm(),
                        "pharmacy": session.register.department == CashRegister.Department.PHARMACY})
    else:
        context["form"] = OpenForm(user=request.user)
    context["history"] = CashSession.objects.filter(cashier=request.user).select_related("register")[:10]
    return render(request, "backoffice/my_cashbox.html", context)


@staff_member_required
@require_POST
def my_cashbox_close(request):
    session = cashbox.user_session(request.user)
    form = CloseForm(request.POST)
    if session and form.is_valid():
        try:
            session = sales.close_session(session, form.cleaned_data["counted_cash"], request.user)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, f"شیفت بسته شد. اختلاف صندوق: {session.difference:,} ریال")
    return redirect("backoffice:my_cashbox")

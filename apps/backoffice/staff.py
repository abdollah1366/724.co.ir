"""مدیریت کارکنان: ساخت حساب، تعیین نقش‌ها، اتصال به پزشک و غیرفعال‌سازی، بدون نیاز به پنل فنی."""

from django import forms
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render

from apps.audit import log
from apps.audit.models import AuditLog
from apps.audit.roles import ROLE_AREAS, ROLE_DESCRIPTIONS
from apps.clinic.models import Doctor

User = get_user_model()


class StaffForm(forms.Form):
    username = forms.CharField(label="نام کاربری", max_length=150, help_text="با همین نام وارد می‌شود؛ حروف انگلیسی و عدد")
    first_name = forms.CharField(label="نام", max_length=150)
    last_name = forms.CharField(label="نام خانوادگی", max_length=150)
    email = forms.EmailField(label="ایمیل", required=False)
    password = forms.CharField(label="رمز عبور", required=False, widget=forms.PasswordInput(render_value=False),
                               help_text="برای کاربر جدید الزامی است؛ برای کاربر موجود فقط اگر می‌خواهید عوض شود.")
    is_active = forms.BooleanField(label="فعال (می‌تواند وارد شود)", required=False, initial=True)
    roles = forms.MultipleChoiceField(label="نقش‌ها", required=False, widget=forms.CheckboxSelectMultiple)
    doctor = forms.ModelChoiceField(label="پزشک / درمانگر متناظر", required=False, queryset=Doctor.objects.none(),
                                    help_text="برای پزشک و درمانگر لازم است تا بیماران و صف خودش را ببیند.")

    def __init__(self, *args, instance=None, grantable=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance = instance
        self.fields["roles"].choices = [
            (area, [(name, name) for name in names if grantable is None or name in grantable]) for area, names in ROLE_AREAS
        ]
        self.fields["doctor"].queryset = Doctor.objects.filter(Q(user__isnull=True) | Q(user=instance) if instance else Q(user__isnull=True))
        if instance:
            self.fields["username"].disabled = True
            self.initial.update({
                "username": instance.username, "first_name": instance.first_name, "last_name": instance.last_name,
                "email": instance.email, "is_active": instance.is_active,
                "roles": list(instance.groups.values_list("name", flat=True)),
                "doctor": getattr(instance, "doctor", None),
            })

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if not self.instance and User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("این نام کاربری قبلاً ثبت شده است.")
        return username

    def clean_password(self):
        password = self.cleaned_data.get("password")
        if not password and not self.instance:
            raise forms.ValidationError("برای کاربر جدید رمز عبور لازم است.")
        if password:
            validate_password(password, self.instance)
        return password


def grantable_roles(user):
    """کاربر غیر superuser فقط نقش‌هایی را می‌دهد که همه مجوزهایشان را خودش دارد (جلوگیری از افزایش دسترسی)."""
    if user.is_superuser:
        return None
    own = user.get_all_permissions()
    return {
        group.name for group in Group.objects.prefetch_related("permissions__content_type")
        if {f"{p.content_type.app_label}.{p.codename}" for p in group.permissions.all()} <= own
    }


def _can_edit(request, user):
    # مدیر کل نمی‌تواند حساب مدیر ارشد سامانه (superuser) را تغییر دهد.
    return request.user.is_superuser or not user.is_superuser


@staff_member_required
@permission_required("auth.view_user", raise_exception=True)
def staff_list(request):
    query = request.GET.get("q", "").strip()
    role = request.GET.get("role", "")
    users = User.objects.filter(is_staff=True).prefetch_related("groups").select_related("doctor").order_by("-is_active", "last_name")
    if query:
        users = users.filter(Q(username__icontains=query) | Q(first_name__icontains=query) | Q(last_name__icontains=query))
    if role:
        users = users.filter(groups__name=role)
    counts = dict(Group.objects.filter(user__is_staff=True, user__is_active=True).values_list("name").annotate(n=Count("user")))
    guide = [(area, [(name, counts.get(name, 0), ROLE_DESCRIPTIONS.get(name, "")) for name in names]) for area, names in ROLE_AREAS]
    return render(request, "backoffice/staff_list.html", {
        "users": users, "query": query, "role": role, "areas": ROLE_AREAS, "guide": guide,
        "can_edit": request.user.has_perm("auth.change_user"),
    })


@staff_member_required
def staff_edit(request, pk=None):
    user = get_object_or_404(User, pk=pk, is_staff=True) if pk else None
    if not request.user.has_perm("auth.change_user" if user else "auth.add_user"):
        raise PermissionDenied
    if user and not _can_edit(request, user):
        raise PermissionDenied
    grantable = grantable_roles(request.user)
    form = StaffForm(request.POST or None, instance=user, grantable=grantable)
    kept = [] if grantable is None or user is None else [g for g in user.groups.values_list("name", flat=True) if g not in grantable]
    if kept:
        # رمز حسابی که نقشی بالاتر از دسترسی ویرایشگر دارد (مثلاً پزشک) را فقط مدیر ارشد عوض می‌کند؛
        # وگرنه مدیر کل با ورود به آن حساب به پرونده بالینی می‌رسید.
        form.fields["password"].disabled = True
        form.fields["password"].help_text = "رمز این حساب را فقط مدیر ارشد سامانه می‌تواند عوض کند."
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        if user and user.pk == request.user.pk and not data["is_active"]:
            form.add_error("is_active", "حساب خودتان را نمی‌توانید غیرفعال کنید.")
        else:
            with transaction.atomic():
                created = user is None
                user = user or User(username=data["username"], is_staff=True)
                user.first_name, user.last_name, user.email = data["first_name"], data["last_name"], data["email"]
                user.is_active = data["is_active"]
                if data["password"]:
                    user.set_password(data["password"])
                user.save()
                before = sorted(user.groups.values_list("name", flat=True)) if not created else []
                user.groups.set(Group.objects.filter(name__in=[*data["roles"], *kept]))
                Doctor.objects.filter(user=user).exclude(pk=getattr(data["doctor"], "pk", None)).update(user=None)
                if data["doctor"]:
                    Doctor.objects.filter(pk=data["doctor"].pk).update(user=user)
                log.record(AuditLog.Action.EVENT, user, message=(
                    f"{'ساخت' if created else 'ویرایش'} حساب کارمند؛ نقش‌ها: {before} ← {sorted(data['roles'])}"
                    f"{'؛ تغییر رمز' if data['password'] and not created else ''}"))
            messages.success(request, f"حساب {user.username} ذخیره شد.")
            return redirect("backoffice:staff")
    return render(request, "backoffice/staff_form.html", {"form": form, "staff_user": user, "descriptions": ROLE_DESCRIPTIONS,
                                                           "kept": kept})

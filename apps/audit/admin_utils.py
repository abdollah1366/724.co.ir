"""پایه ادمین اسناد: ثبت نهایی و برگشت با کنترل مجوز، و فقط‌خواندنی شدن سند نهایی."""

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse

from .documents import DocStatus


class LockedInlineMixin:
    """ردیف‌های سند پس از ثبت نهایی فقط‌خواندنی هستند. سند پیش‌نویس یک ردیف خالی آماده دارد."""

    extra = 0

    def get_extra(self, request, obj=None, **kwargs):
        # یک ردیف خالی در سند پیش‌نویس تا کاربر دنبال «افزودن ردیف» نگردد؛ ردیف خالی ذخیره نمی‌شود.
        return 0 if self._locked(obj) else 1

    def _locked(self, obj):
        return obj is not None and obj.status != DocStatus.DRAFT

    def has_add_permission(self, request, obj=None):
        return not self._locked(obj) and super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return not self._locked(obj) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return not self._locked(obj) and super().has_delete_permission(request, obj)


class ReverseForm(forms.Form):
    reason = forms.CharField(label="علت برگشت", max_length=300, widget=forms.Textarea(attrs={"rows": 3}))


class DocumentAdmin(admin.ModelAdmin):
    change_form_template = "admin/erp/document_change_form.html"
    doc_readonly = ("number", "status", "created_by", "posted_by", "posted_at", "reversed_by", "reversed_at", "reverse_reason")
    list_filter = ("status",)
    date_hierarchy = "date"

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.status != DocStatus.DRAFT:
            return [f.name for f in self.model._meta.fields]
        return list(self.doc_readonly) + list(super().get_readonly_fields(request, obj))

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.status != DocStatus.DRAFT:
            return False
        return super().has_delete_permission(request, obj)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def _perm(self, action):
        opts = self.model._meta
        return f"{opts.app_label}.{action}_{opts.model_name}"

    def get_urls(self):
        info = self.model._meta.app_label, self.model._meta.model_name
        return [
            path("<int:pk>/post/", self.admin_site.admin_view(self.post_view), name="%s_%s_post" % info),
            path("<int:pk>/reverse/", self.admin_site.admin_view(self.reverse_view), name="%s_%s_reverse" % info),
        ] + super().get_urls()

    def render_change_form(self, request, context, add=False, change=False, form_url="", obj=None):
        info = self.model._meta.app_label, self.model._meta.model_name
        if obj is not None:
            context["can_post_document"] = obj.status == DocStatus.DRAFT and request.user.has_perm(self._perm("post"))
            context["can_reverse_document"] = (
                obj.status == DocStatus.POSTED and request.user.has_perm(self._perm("reverse"))
            )
            context["post_url"] = reverse("admin:%s_%s_post" % info, args=[obj.pk])
            context["reverse_url"] = reverse("admin:%s_%s_reverse" % info, args=[obj.pk])
        return super().render_change_form(request, context, add, change, form_url, obj)

    def _change_url(self, obj):
        info = self.model._meta.app_label, self.model._meta.model_name
        return reverse("admin:%s_%s_change" % info, args=[obj.pk])

    def post_view(self, request, pk):
        if request.method != "POST":
            return redirect(self._change_url(get_object_or_404(self.model, pk=pk)))
        if not request.user.has_perm(self._perm("post")):
            raise PermissionDenied
        obj = get_object_or_404(self.model, pk=pk)
        try:
            obj.post(request.user)
        except ValidationError as exc:
            self.message_user(request, " ".join(exc.messages), messages.ERROR)
        else:
            self.message_user(request, f"سند {obj.number} ثبت نهایی شد.", messages.SUCCESS)
        return redirect(self._change_url(obj))

    def reverse_view(self, request, pk):
        if not request.user.has_perm(self._perm("reverse")):
            raise PermissionDenied
        obj = get_object_or_404(self.model, pk=pk)
        form = ReverseForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            try:
                obj.reverse(request.user, form.cleaned_data["reason"])
            except ValidationError as exc:
                self.message_user(request, " ".join(exc.messages), messages.ERROR)
            else:
                self.message_user(request, f"سند {obj.number} برگشت خورد و سند معکوس ثبت شد.", messages.SUCCESS)
            return redirect(self._change_url(obj))
        context = {**self.admin_site.each_context(request), "form": form, "object": obj, "opts": self.model._meta,
                   "title": f"برگشت سند {obj.number}"}
        return render(request, "admin/erp/reverse_document.html", context)

    @admin.action(description="ثبت نهایی اسناد انتخاب‌شده")
    def post_selected(self, request, queryset):
        if not request.user.has_perm(self._perm("post")):
            raise PermissionDenied
        done = 0
        for obj in queryset.filter(status=DocStatus.DRAFT):
            try:
                obj.post(request.user)
                done += 1
            except ValidationError as exc:
                self.message_user(request, f"{obj}: {' '.join(exc.messages)}", messages.ERROR)
        if done:
            self.message_user(request, f"{done} سند ثبت نهایی شد.", messages.SUCCESS)

    actions = ["post_selected"]


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.http import HttpResponseRedirect

from .models import Category, Post, Tag

# دکمه‌های گردش کار در فرم مقاله: نام دکمه ← (متد مدل، مجوز لازم، پیام موفقیت)
WORKFLOW = {
    "_wf_submit": ("submit", None, "مقاله برای بازبینی علمی ارسال شد."),
    "_wf_approve": ("approve", "blog.review_post", "مقاله از نظر علمی تأیید شد."),
    "_wf_reject": ("reject", "blog.review_post", "مقاله برای اصلاح به نویسنده برگشت."),
    "_wf_publish": ("publish", "blog.publish_post", "مقاله منتشر شد."),
    "_wf_archive": ("archive", "blog.publish_post", "مقاله بایگانی شد و از سایت برداشته شد."),
}


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "requires_medical_review", "order", "is_active")
    list_editable = ("order", "is_active")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}


def _is_editor(user):
    return user.has_perm("blog.publish_post") or user.has_perm("blog.review_post")


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    change_form_template = "admin/blog/post_change_form.html"
    list_display = ("title", "category", "status", "author", "reviewer", "published_at")
    list_filter = ("status", "category", "is_featured")
    search_fields = ("title", "summary", "body")
    autocomplete_fields = ("tags",)
    filter_horizontal = ("departments",)
    prepopulated_fields = {"slug": ("title",)}
    date_hierarchy = "created_at"
    fieldsets = (
        (None, {"fields": ("title", "slug", "category", "summary", "body", "references")}),
        ("تصویر و نمایش", {"fields": ("cover", "cover_alt", "tags", "departments", "is_featured")}),
        ("سئو", {"fields": ("meta_title", "meta_description"), "classes": ("collapse",)}),
        ("نویسنده و بازبینی", {"fields": (
            "status", "author", "author_display", "reviewer", "reviewer_display", "reviewed_at", "review_note", "published_at",
        )}),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("category", "author", "reviewer")
        # نویسنده فقط مقاله‌های خودش را می‌بیند.
        return qs if _is_editor(request.user) else qs.filter(author=request.user)

    def get_readonly_fields(self, request, obj=None):
        fields = ["status", "author", "reviewer", "reviewed_at"]
        if not request.user.has_perm("blog.review_post"):
            fields += ["review_note", "reviewer_display"]
        if not request.user.has_perm("blog.publish_post"):
            fields += ["published_at", "is_featured", "author_display"]
        return fields

    def has_change_permission(self, request, obj=None):
        if not super().has_change_permission(request, obj):
            return False
        if obj is None or _is_editor(request.user):
            return True
        # نویسنده پس از ارسال برای بازبینی دیگر نمی‌تواند متن را تغییر دهد.
        return obj.author_id == request.user.pk and obj.status == Post.Status.DRAFT

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.status == Post.Status.PUBLISHED:
            return False  # مقاله منتشرشده بایگانی می‌شود، حذف نمی‌شود.
        return super().has_delete_permission(request, obj) and (
            obj is None or _is_editor(request.user) or obj.author_id == request.user.pk
        )

    def save_model(self, request, obj, form, change):
        if not change:
            obj.author = request.user
        super().save_model(request, obj, form, change)

    def _allowed_actions(self, request, obj):
        user = request.user
        allowed = []
        if obj.status == Post.Status.DRAFT and (obj.author_id == user.pk or user.has_perm("blog.publish_post")):
            allowed.append("_wf_submit")
        if obj.status == Post.Status.IN_REVIEW and user.has_perm("blog.review_post") and obj.author_id != user.pk:
            allowed += ["_wf_approve", "_wf_reject"]
        if obj.status in (Post.Status.APPROVED, Post.Status.ARCHIVED) and user.has_perm("blog.publish_post"):
            allowed.append("_wf_publish")
        if obj.status == Post.Status.PUBLISHED and user.has_perm("blog.publish_post"):
            allowed.append("_wf_archive")
        return allowed

    def render_change_form(self, request, context, add=False, change=False, form_url="", obj=None):
        if obj is not None:
            context["workflow_actions"] = self._allowed_actions(request, obj)
        return super().render_change_form(request, context, add, change, form_url, obj)

    def _run_workflow(self, request, obj):
        for name in self._allowed_actions(request, obj):
            if name not in request.POST:
                continue
            method, _perm, success = WORKFLOW[name]
            try:
                if method in ("approve", "reject"):
                    getattr(obj, method)(request.user, obj.review_note)
                else:
                    getattr(obj, method)()
            except ValidationError as exc:
                self.message_user(request, " ".join(exc.messages), messages.ERROR)
            else:
                self.message_user(request, success, messages.SUCCESS)
            return HttpResponseRedirect(request.path)
        return None

    def response_change(self, request, obj):
        # ذخیره عادی فرم انجام شده است؛ سپس در صورت فشردن دکمه گردش کار، انتقال وضعیت انجام می‌شود.
        return self._run_workflow(request, obj) or super().response_change(request, obj)

    def response_add(self, request, obj, post_url_continue=None):
        return self._run_workflow(request, obj) or super().response_add(request, obj, post_url_continue)

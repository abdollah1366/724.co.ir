import datetime
import tempfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.blog import markup
from apps.blog.models import Post, PostImage
from apps.blog.tests.test_blog import BlogTestCase, make_post
from apps.clinic.tests.factories import make_doctor
from apps.inventory.tests.fixtures import staff

User = get_user_model()
PASSWORD = "Strong-pass-724!"
GIF = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,"
       b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;")


def roles_of(user):
    return sorted(user.groups.values_list("name", flat=True))


class BlogManagerStaffTests(TestCase):
    def setUp(self):
        self.manager = staff("blogman", "مدیر وبلاگ")
        self.client.force_login(self.manager)

    def test_blog_manager_has_no_general_user_permissions(self):
        self.assertFalse(self.manager.has_perm("auth.change_user"))
        self.assertTrue(self.manager.has_perm("blog.manage_blog_staff"))
        # پنل فنی کاربران (که می‌تواند superuser بسازد) برای او بسته است.
        self.assertEqual(self.client.get(reverse("admin:auth_user_changelist")).status_code, 403)

    def test_creates_author_with_only_blog_roles(self):
        page = self.client.get(reverse("backoffice:staff_new"))
        self.assertContains(page, 'value="نویسنده محتوا"')
        self.assertContains(page, 'value="بازبین علمی محتوا"')
        self.assertNotContains(page, 'value="مدیر وبلاگ"')
        self.assertNotContains(page, 'value="انباردار"')
        self.assertNotContains(page, 'name="doctor"')
        response = self.client.post(reverse("backoffice:staff_new"), {
            "username": "writer", "first_name": "سارا", "last_name": "نویسا", "password": PASSWORD, "is_active": "on",
            "roles": ["نویسنده محتوا"],
        })
        self.assertRedirects(response, reverse("backoffice:staff"))
        writer = User.objects.get(username="writer")
        self.assertTrue(writer.is_staff and writer.check_password(PASSWORD))
        self.assertEqual(roles_of(writer), ["نویسنده محتوا"])

    def test_cannot_grant_other_roles(self):
        response = self.client.post(reverse("backoffice:staff_new"), {
            "username": "x", "first_name": "x", "last_name": "x", "password": PASSWORD, "is_active": "on",
            "roles": ["مدیر وبلاگ", "انباردار"],
        })
        self.assertEqual(response.status_code, 200)  # انتخاب نامعتبر
        self.assertFalse(User.objects.filter(username="x").exists())

    def test_adds_reviewer_role_to_doctor_without_touching_account(self):
        doc_user = staff("drkarimi", "پزشک")
        doc_user.first_name = "کریم"
        doc_user.set_password(PASSWORD)
        doc_user.save()
        make_doctor(user=doc_user)
        listing = self.client.get(reverse("backoffice:staff"))
        self.assertNotContains(listing, "drkarimi")  # همکاران غیروبلاگی فقط با جست‌وجو
        self.assertContains(self.client.get(reverse("backoffice:staff"), {"q": "drkarimi"}), "drkarimi")

        url = reverse("backoffice:staff_edit", args=[doc_user.pk])
        self.assertContains(self.client.get(url), "فقط نقش‌های وبلاگی")
        self.client.post(url, {
            "first_name": "تغییر", "last_name": "x", "password": "Other-pass-999!", "roles": ["بازبین علمی محتوا"],
        })
        doc_user.refresh_from_db()
        self.assertEqual(roles_of(doc_user), ["بازبین علمی محتوا", "پزشک"])
        self.assertTrue(doc_user.check_password(PASSWORD))
        self.assertTrue(doc_user.is_active)
        self.assertEqual(doc_user.first_name, "کریم")
        self.assertEqual(doc_user.doctor.user, doc_user)

    def test_can_reset_password_of_blog_only_staff(self):
        writer = staff("writer", "نویسنده محتوا")
        self.client.post(reverse("backoffice:staff_edit", args=[writer.pk]), {
            "first_name": "سارا", "last_name": "ن", "password": PASSWORD, "is_active": "on", "roles": ["بازبین علمی محتوا"],
        })
        writer.refresh_from_db()
        self.assertTrue(writer.check_password(PASSWORD))
        self.assertEqual(roles_of(writer), ["بازبین علمی محتوا"])

    def test_cannot_edit_superuser_and_others_have_no_access(self):
        root = staff("root", superuser=True)
        self.assertEqual(self.client.get(reverse("backoffice:staff_edit", args=[root.pk])).status_code, 403)
        self.client.force_login(staff("writer", "نویسنده محتوا"))
        self.assertEqual(self.client.get(reverse("backoffice:staff")).status_code, 403)
        self.assertEqual(self.client.get(reverse("backoffice:staff_new")).status_code, 403)

    def test_general_manager_keeps_full_staff_page(self):
        self.client.force_login(staff("gm", "مدیر کل"))
        page = self.client.get(reverse("backoffice:staff_new"))
        self.assertContains(page, 'value="انباردار"')
        self.assertContains(page, 'value="مدیر وبلاگ"')
        self.assertContains(page, 'name="doctor"')

    def test_old_content_manager_group_is_renamed(self):
        Group.objects.filter(name="مدیر وبلاگ").update(name="مدیر محتوا")
        from importlib import import_module

        from django.apps import apps
        import_module("apps.blog.migrations.0002_doctor_images_staff").rename_role(apps, None)
        self.assertTrue(Group.objects.filter(name="مدیر وبلاگ").exists())
        self.assertFalse(Group.objects.filter(name="مدیر محتوا").exists())


class BlogFeatureTests(BlogTestCase):
    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_inline_images(self):
        post = make_post(self.author, self.medical, body="متن\n\n[تصویر ۱]\n\n[تصویر 9]")
        image = PostImage(post=post, number=1, alt="نحوه بستن بازوبند", caption="بازوبند هم‌سطح قلب")
        image.image.save("cuff.gif", SimpleUploadedFile("cuff.gif", GIF), save=True)
        html = markup.render(post.body, {1: image})
        self.assertIn('alt="نحوه بستن بازوبند"', html)
        self.assertIn("<figcaption>بازوبند هم‌سطح قلب</figcaption>", html)
        self.assertNotIn("تصویر 9", html)  # شماره ناموجود نادیده گرفته می‌شود
        self.publish(post)
        self.assertContains(self.client.get(post.get_absolute_url()), "post-figure")

    def test_doctor_link_views_and_share(self):
        doc = make_doctor()
        post = self.publish(make_post(self.author, self.medical, doctor=doc))
        page = self.client.get(post.get_absolute_url())
        self.assertContains(page, "درباره نویسنده")
        self.assertContains(page, doc.get_absolute_url())
        self.assertContains(page, "https://wa.me/?text=")
        self.assertContains(page, "https://t.me/share/url")
        self.client.get(post.get_absolute_url())
        post.refresh_from_db()
        self.assertEqual(post.views, 2)
        self.client.force_login(self.editor)  # بازدید کارکنان شمرده نمی‌شود
        self.client.get(post.get_absolute_url())
        post.refresh_from_db()
        self.assertEqual(post.views, 2)
        self.assertContains(self.client.get(doc.get_absolute_url()), post.title)
        self.client.logout()
        self.assertContains(self.client.get(reverse("blog:list")), "پربازدیدترین‌ها")

    def test_periodic_review(self):
        post = self.publish(make_post(self.author, self.medical))
        Post.objects.filter(pk=post.pk).update(reviewed_at=timezone.now() - datetime.timedelta(days=400))
        post.refresh_from_db()
        self.assertTrue(post.review_overdue)
        self.client.force_login(self.editor)
        self.assertContains(self.client.get(reverse("blog:manage")), "نیاز به بازبینی دوره‌ای")
        other = staff("reviewer2", "بازبین علمی محتوا")
        post.reconfirm(other)
        self.assertFalse(post.review_overdue)
        self.assertTrue(post.is_live)
        self.assertEqual(post.reviewer, other)

    def test_manage_dashboard_per_role(self):
        mine = make_post(self.author, self.medical, title="مقاله من")
        mine.submit()
        mine.reject(self.reviewer, "منبع اضافه شود")
        waiting = make_post(self.editor, self.medical, title="منتظر بازبینی", slug="w")
        waiting.submit()

        self.client.force_login(self.author)
        page = self.client.get(reverse("blog:manage"))
        self.assertContains(page, "برگشت‌خورده برای اصلاح")
        self.assertContains(page, "منبع اضافه شود")
        self.assertNotContains(page, "منتظر بازبینی</a>")

        self.client.force_login(self.reviewer)
        page = self.client.get(reverse("blog:manage"))
        self.assertContains(page, "منتظر بازبینی علمی شما")
        self.assertContains(page, "منتظر بازبینی</a>")

        self.client.force_login(staff("cashier", "صندوق‌دار"))
        self.assertEqual(self.client.get(reverse("blog:manage")).status_code, 403)

    def test_reconfirm_button_in_admin(self):
        post = self.publish(make_post(self.author, self.medical))
        self.client.force_login(staff("reviewer2", "بازبین علمی محتوا"))
        self.assertContains(self.client.get(reverse("admin:blog_post_change", args=[post.pk])), 'name="_wf_reconfirm"')

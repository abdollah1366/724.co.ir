import datetime
import io
import json
import re

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.blog import markup
from apps.blog.models import Category, Post
from apps.clinic.models import Department
from apps.inventory.tests.fixtures import staff


def make_post(author, category, **kwargs):
    defaults = {"title": "فشار خون", "slug": "فشار-خون", "summary": "خلاصه", "body": "## بخش یک\nمتن"}
    defaults.update(kwargs)
    return Post.objects.create(author=author, category=category, **defaults)


class BlogTestCase(TestCase):
    def setUp(self):
        self.medical = Category.objects.create(name="بیماری‌های مزمن", slug="chronic")
        self.news = Category.objects.create(name="اخبار", slug="news", requires_medical_review=False)
        self.author = staff("author", "نویسنده محتوا")
        self.reviewer = staff("reviewer", "بازبین علمی محتوا")
        self.editor = staff("editor", "مدیر وبلاگ")

    def publish(self, post):
        post.submit()
        post.approve(self.reviewer)
        post.publish()
        return post


class WorkflowTests(BlogTestCase):
    def test_medical_post_needs_review_before_publish(self):
        post = make_post(self.author, self.medical)
        with self.assertRaises(ValidationError):
            post.publish()
        post.submit()
        self.assertEqual(post.status, Post.Status.IN_REVIEW)
        with self.assertRaises(ValidationError):
            post.publish()
        post.approve(self.reviewer)
        post.publish()
        self.assertTrue(post.is_live)
        self.assertEqual(post.reviewer, self.reviewer)

    def test_author_cannot_review_own_post(self):
        post = make_post(self.reviewer, self.medical)
        post.submit()
        with self.assertRaises(ValidationError):
            post.approve(self.reviewer)

    def test_reject_requires_note_and_returns_to_draft(self):
        post = make_post(self.author, self.medical)
        post.submit()
        with self.assertRaises(ValidationError):
            post.reject(self.reviewer, "")
        post.reject(self.reviewer, "دوز دارو منبع ندارد")
        self.assertEqual(post.status, Post.Status.DRAFT)

    def test_news_skips_medical_review(self):
        post = make_post(self.author, self.news)
        post.submit()
        self.assertEqual(post.status, Post.Status.APPROVED)
        post.publish()
        self.assertTrue(post.is_live)

    def test_editing_reviewed_content_takes_post_offline_for_rereview(self):
        post = self.publish(make_post(self.author, self.medical))
        post.is_featured = True
        post.save()
        self.assertEqual(post.status, Post.Status.PUBLISHED)  # تغییر غیرعلمی مشکلی ندارد
        post.body = "متن تغییر کرد"
        post.save()
        post.refresh_from_db()
        self.assertEqual(post.status, Post.Status.IN_REVIEW)
        self.assertIsNone(post.reviewer)
        self.assertFalse(Post.objects.published().exists())

    def test_scheduled_post_is_hidden_until_its_time(self):
        post = make_post(self.author, self.medical)
        post.submit()
        post.approve(self.reviewer)
        post.publish(when=timezone.now() + datetime.timedelta(days=1))
        self.assertFalse(Post.objects.published().exists())
        self.assertEqual(self.client.get(post.get_absolute_url()).status_code, 404)

    def test_status_changes_are_audited(self):
        post = make_post(self.author, self.medical)
        post.submit()
        entry = AuditLog.objects.filter(model="blog.post", action=AuditLog.Action.UPDATE).latest("pk")
        self.assertEqual(entry.changes["status"], ["draft", "in_review"])


class MarkupTests(TestCase):
    def test_html_is_escaped(self):
        html = markup.render('<script>alert(1)</script>\n\n**پررنگ** [x](javascript:alert(1))')
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("<strong>پررنگ</strong>", html)
        self.assertNotIn('href="javascript', html)

    def test_blocks(self):
        html = markup.render("## عنوان\nمتن\n\n- یک\n- دو\n\n1. اول\n2. دوم\n\n> هشدار\n\n[نوبت](/doctors/) [منبع](https://example.org)")
        self.assertIn('<h2 id="s1">عنوان</h2>', html)
        self.assertIn("<ul><li>یک</li><li>دو</li></ul>", html)
        self.assertIn("<ol><li>اول</li><li>دوم</li></ol>", html)
        self.assertIn('<aside class="callout">هشدار</aside>', html)
        self.assertIn('<a href="/doctors/">نوبت</a>', html)
        self.assertIn('rel="nofollow noopener"', html)
        self.assertNotIn('href="//', markup.render("[x](//evil.example)"))
        self.assertEqual(markup.headings("## الف\nx\n## ب"), [("s1", "الف"), ("s2", "ب")])


class PublicPageTests(BlogTestCase):
    def test_list_detail_category_search_and_feed(self):
        live = self.publish(make_post(self.author, self.medical, title="کنترل فشار خون", references="منبع یک"))
        draft = make_post(self.author, self.medical, title="پیش‌نویس", slug="draft")

        response = self.client.get(reverse("blog:list"))
        self.assertContains(response, "کنترل فشار خون")
        self.assertNotContains(response, "پیش‌نویس")
        self.assertContains(self.client.get(reverse("blog:list"), {"q": "کنترل"}), "کنترل فشار خون")
        self.assertNotContains(self.client.get(reverse("blog:list"), {"q": "هیچ"}), "کنترل فشار خون")
        self.assertContains(self.client.get(self.medical.get_absolute_url()), "کنترل فشار خون")

        detail = self.client.get(live.get_absolute_url())
        self.assertContains(detail, "بازبینی علمی:")
        self.assertContains(detail, "جایگزین معاینه")
        self.assertContains(detail, "منبع یک")
        data = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', detail.content.decode(), re.S).group(1))
        self.assertEqual(data["@type"], "MedicalWebPage")
        self.assertIn("reviewedBy", data)

        self.assertEqual(self.client.get(draft.get_absolute_url()).status_code, 404)
        self.assertContains(self.client.get(reverse("blog:feed")), "کنترل فشار خون")

    def test_staff_can_preview_draft_with_noindex(self):
        draft = make_post(self.author, self.medical)
        self.client.force_login(self.author)
        response = self.client.get(draft.get_absolute_url())
        self.assertContains(response, "پیش‌نمایش")
        self.assertContains(response, "noindex")

    def test_post_shows_on_home_and_department_and_sitemap(self):
        dept = Department.objects.create(title="درمانگاه", slug="clinic", summary="x", kind=Department.Kind.CLINIC)
        post = self.publish(make_post(self.author, self.medical))
        post.departments.add(dept)
        self.assertContains(self.client.get(reverse("core:home")), post.title)
        self.assertContains(self.client.get(dept.get_absolute_url()), post.title)
        sitemap = self.client.get("/sitemap.xml").content.decode()
        self.assertIn(post.get_absolute_url(), sitemap)
        self.assertContains(self.client.get("/robots.txt"), "Disallow: /ehr/")


class AdminTests(BlogTestCase):
    def change_url(self, post):
        return reverse("admin:blog_post_change", args=[post.pk])

    def form_data(self, post, **extra):
        data = {"title": post.title, "slug": post.slug, "category": post.category_id, "summary": post.summary,
                "body": post.body, "references": post.references, "cover_alt": "", "meta_title": "",
                "meta_description": "", "author_display": "", "reviewer_display": "", "review_note": post.review_note,
                "doctor": "", "images-TOTAL_FORMS": "0", "images-INITIAL_FORMS": "0"}
        data.update(extra)
        return data

    def test_author_sees_only_own_posts_and_cannot_edit_after_submit(self):
        own = make_post(self.author, self.medical)
        other = make_post(self.editor, self.medical, slug="other", title="دیگری")
        self.client.force_login(self.author)
        changelist = self.client.get(reverse("admin:blog_post_changelist"))
        self.assertContains(changelist, own.title)
        self.assertNotContains(changelist, "دیگری")
        self.assertEqual(self.client.get(self.change_url(other)).status_code, 302)  # پیدا نشد → بازگشت به فهرست

        response = self.client.post(self.change_url(own), self.form_data(own, _wf_submit="1"))
        self.assertEqual(response.status_code, 302)
        own.refresh_from_db()
        self.assertEqual(own.status, Post.Status.IN_REVIEW)
        response = self.client.post(self.change_url(own), self.form_data(own, body="تغییر"))
        self.assertEqual(response.status_code, 403)

    def test_author_cannot_publish_or_approve(self):
        post = make_post(self.author, self.medical)
        post.submit()
        self.client.force_login(self.author)
        self.client.post(self.change_url(post), self.form_data(post, _wf_approve="1"))
        post.refresh_from_db()
        self.assertEqual(post.status, Post.Status.IN_REVIEW)

    def test_reviewer_then_editor_publish_via_admin(self):
        post = make_post(self.author, self.medical)
        post.submit()
        self.client.force_login(self.reviewer)
        self.client.post(self.change_url(post), self.form_data(post, review_note="منابع کافی است", _wf_approve="1"))
        post.refresh_from_db()
        self.assertEqual((post.status, post.reviewer, post.review_note), (Post.Status.APPROVED, self.reviewer, "منابع کافی است"))
        # بازبین مجوز انتشار ندارد
        self.client.post(self.change_url(post), self.form_data(post, _wf_publish="1"))
        post.refresh_from_db()
        self.assertEqual(post.status, Post.Status.APPROVED)

        self.client.force_login(self.editor)
        page = self.client.get(self.change_url(post))
        self.assertContains(page, 'name="_wf_publish"')
        self.client.post(self.change_url(post), self.form_data(post, _wf_publish="1"))
        post.refresh_from_db()
        self.assertTrue(post.is_live)
        self.assertEqual(self.client.post(reverse("admin:blog_post_delete", args=[post.pk]), {"post": "yes"}).status_code, 403)

    def test_seed_blog_demo(self):
        call_command("seed_blog", "--demo", stdout=io.StringIO())
        call_command("seed_blog", "--demo", stdout=io.StringIO())  # تکرارپذیر
        self.assertEqual(Post.objects.published().count(), 3)
        self.assertTrue(Post.objects.filter(category__slug="chronic", reviewer__username="blog_reviewer").exists())

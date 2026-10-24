from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from apps.core.middleware import RealIPMiddleware


def seen_ip(**meta):
    request = RequestFactory().get("/", REMOTE_ADDR="", **meta)
    RealIPMiddleware(lambda req: HttpResponse(req.META["REMOTE_ADDR"]))(request)
    return request.META["REMOTE_ADDR"]


class RealIPMiddlewareTests(SimpleTestCase):
    @override_settings(BEHIND_PROXY=True)
    def test_uses_header_behind_proxy(self):
        self.assertEqual(seen_ip(HTTP_X_REAL_IP="5.6.7.8"), "5.6.7.8")

    @override_settings(BEHIND_PROXY=True)
    def test_ignores_invalid_header(self):
        self.assertEqual(seen_ip(HTTP_X_REAL_IP="5.6.7.8, 1.1.1.1"), "")

    @override_settings(BEHIND_PROXY=False)
    def test_header_ignored_without_proxy(self):
        self.assertEqual(seen_ip(HTTP_X_REAL_IP="5.6.7.8"), "")


class PublicMediaTests(SimpleTestCase):
    def test_serves_file_with_sandbox_csp(self):
        import tempfile
        from pathlib import Path

        from apps.core.views import public_media

        with tempfile.TemporaryDirectory() as root:
            Path(root, "a.png").write_bytes(b"\x89PNG")
            with override_settings(MEDIA_ROOT=root):
                response = public_media(RequestFactory().get("/media/a.png"), "a.png")
                self.assertEqual(response.status_code, 200)
                self.assertIn("sandbox", response["Content-Security-Policy"])
                from django.core.exceptions import SuspiciousFileOperation

                with self.assertRaises(SuspiciousFileOperation):
                    public_media(RequestFactory().get("/media/../x"), "../settings.py")

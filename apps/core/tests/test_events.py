import datetime

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.core import events, flags
from apps.core.models import FeatureFlag, OutboxEvent
from apps.inventory.tests.fixtures import staff

Status = OutboxEvent.Status
calls = []
failures = {"left": 0}


@events.subscribe("test.thing.happened", flag="test.feature")
def _handler(event):
    if failures["left"]:
        failures["left"] -= 1
        raise RuntimeError("temporary outage")
    calls.append(event.payload["n"])
    return f"done-{event.payload['n']}"


flags.register("test", "آزمون", default=True)
flags.register("test.feature", "قابلیت آزمون", default=True)
flags.register("test.needs", "وابسته", default=True, requires=["test.feature"])
flags.register("test.core", "هسته", default=True, critical=True)


class EventBusTests(TestCase):
    def setUp(self):
        calls.clear()
        failures["left"] = 0
        flags.reset_cache()
        self.addCleanup(flags.reset_cache)
        self.user = staff("admin1", superuser=True)

    def publish(self, n=1, **kw):
        return events.publish("test.thing.happened", "test.source", {"n": n}, source_id=n, **kw)

    def test_publish_is_idempotent_and_carries_metadata(self):
        first = self.publish()
        again = self.publish()
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(OutboxEvent.objects.count(), 1)
        self.assertTrue(first.event_id)
        self.assertEqual((first.schema_version, first.source_type, first.source_id), (1, "test.source", "1"))
        self.assertIsNone(events.publish("nobody.listens", "x", {}, source_id=1))

    def test_dispatch_processes_once(self):
        event = self.publish(7)
        self.assertEqual(events.dispatch()["processed"], 1)
        self.assertEqual(events.dispatch()["processed"], 0)
        self.assertEqual(events.process(event.pk), Status.PROCESSED)  # پردازش دوباره بی‌اثر
        event.refresh_from_db()
        self.assertEqual((event.status, event.result_ref, calls), (Status.PROCESSED, "done-7", [7]))

    def test_failure_backoff_then_success(self):
        failures["left"] = 1
        event = self.publish()
        self.assertEqual(events.dispatch()["failed"], 1)
        event.refresh_from_db()
        self.assertEqual((event.status, event.attempts), (Status.FAILED, 1))
        self.assertIn("RuntimeError", event.last_error)
        self.assertEqual(events.dispatch()["processed"], 0)  # هنوز سررسید نشده
        later = timezone.now() + datetime.timedelta(minutes=5)
        self.assertEqual(events.dispatch(now=later)["processed"], 1)

    @override_settings(EVENTS_MAX_ATTEMPTS=2)
    def test_dead_letter_and_manual_retry(self):
        failures["left"] = 5
        event = self.publish()
        events.process(event.pk)
        self.assertEqual(events.process(event.pk), Status.DEAD)
        self.assertEqual(events.health()["dead"], 1)
        failures["left"] = 0
        self.assertEqual(events.retry(OutboxEvent.objects.all()), 1)
        self.assertEqual(events.dispatch()["processed"], 1)

    def test_flag_off_holds_events_and_reenable_releases_them(self):
        flags.set_flag("test.feature", False, self.user, "آزمون خاموش")
        event = self.publish()
        self.assertEqual(events.process(event.pk), "held")
        self.assertEqual(events.dispatch()["processed"], 0)
        self.assertEqual(events.health()["held"], 1)
        event.refresh_from_db()
        self.assertEqual(event.status, Status.PENDING)
        flags.set_flag("test.feature", True, self.user, "روشن")
        self.assertEqual(events.dispatch()["processed"], 1)
        self.assertEqual(calls, [1])

    def test_inline_dispatch_after_commit(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.publish(3)
        self.assertEqual(calls, [3])

    def test_correlation_id_from_request(self):
        from apps.core import middleware

        token = middleware._request_id.set("abc123")
        self.addCleanup(middleware._request_id.reset, token)
        self.assertEqual(self.publish().correlation_id, "abc123")


class FlagTests(TestCase):
    def setUp(self):
        flags.reset_cache()
        self.addCleanup(flags.reset_cache)
        self.user = staff("admin2", superuser=True)

    def test_parent_and_dependency_block_children(self):
        self.assertTrue(flags.is_enabled("test.needs"))
        flags.set_flag("test", False, self.user, "خاموش کردن والد")
        self.assertEqual(flags.blockers("test.needs"), ["test"])
        self.assertFalse(flags.is_enabled("test.feature"))
        flags.set_flag("test", True, self.user, "روشن")
        flags.set_flag("test.feature", False, self.user, "خاموش")
        self.assertEqual(flags.blockers("test.needs"), ["test.feature"])

    def test_note_required_critical_protected_and_audited(self):
        with self.assertRaises(ValidationError):
            flags.set_flag("test.feature", False, self.user, "")
        with self.assertRaises(ValidationError):
            flags.set_flag("test.core", False, self.user, "x")
        with self.assertRaises(ValidationError):
            flags.set_flag("unknown.key", False, self.user, "x")
        flags.set_flag("test.feature", False, self.user, "تعمیرات")
        self.assertTrue(AuditLog.objects.filter(message__contains="test.feature").exists())
        self.assertEqual(FeatureFlag.objects.get(key="test.feature").updated_by, self.user)

    def test_settings_override_and_db_precedence(self):
        with self.settings(FEATURE_FLAGS={"test.feature": False}):
            self.assertFalse(flags.is_enabled("test.feature"))
            FeatureFlag.objects.create(key="test.feature", enabled=True, note="x")
            flags.reset_cache()
            self.assertTrue(flags.is_enabled("test.feature"))

    def test_turning_off_keeps_data(self):
        event = events.publish("test.thing.happened", "test.source", {"n": 9}, source_id=9)
        flags.set_flag("test.feature", False, self.user, "خاموش")
        self.assertTrue(OutboxEvent.objects.filter(pk=event.pk).exists())


class RequestIdTests(TestCase):
    def test_request_id_header_is_echoed_or_generated(self):
        response = self.client.get("/", HTTP_X_REQUEST_ID="req-12345678")
        self.assertEqual(response["X-Request-ID"], "req-12345678")
        generated = self.client.get("/", HTTP_X_REQUEST_ID="bad id with spaces")["X-Request-ID"]
        self.assertNotEqual(generated, "bad id with spaces")
        self.assertEqual(len(generated), 32)

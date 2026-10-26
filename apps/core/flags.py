"""قابلیت‌های قابل روشن/خاموش (feature flags) با سلسله‌مراتب و وابستگی.

هر اپ قابلیت‌هایش را در ready() با register تعریف می‌کند. مقدار مؤثر به این ترتیب تعیین می‌شود:
ردیف FeatureFlag در پایگاه داده (تغییر از صفحه تنظیمات) ← FEATURE_FLAGS در تنظیمات/محیط ← پیش‌فرض تعریف.
یک قابلیت فقط وقتی روشن است که خودش، همه والدهایش (finance ← finance.payroll) و همه وابستگی‌هایش روشن باشند.

قواعد:
- خاموش کردن هیچ داده‌ای را پاک نمی‌کند و جدولی حذف نمی‌شود؛ رویدادهای مربوط در صف «در انتظار» می‌مانند.
- feature flag جایگزین کنترل دسترسی نیست؛ مجوزها جداگانه بررسی می‌شوند.
- قابلیت‌های critical (مثل هسته حسابداری) اصلاً قابل خاموش کردن از صفحه نیستند.
"""

import dataclasses

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError

CACHE_KEY = "feature-flags-v1"
CACHE_SECONDS = 30


@dataclasses.dataclass(frozen=True)
class Flag:
    key: str
    label: str
    default: bool = True
    requires: tuple = ()
    description: str = ""
    critical: bool = False

    @property
    def parent(self):
        return self.key.rsplit(".", 1)[0] if "." in self.key else None


_registry: dict[str, Flag] = {}


def register(key, label, default=True, requires=(), description="", critical=False):
    flag = Flag(key, label, default, tuple(requires), description, critical)
    _registry[key] = flag
    return flag


def registry():
    return dict(sorted(_registry.items()))


def _stored():
    data = cache.get(CACHE_KEY)
    if data is None:
        from .models import FeatureFlag

        data = dict(FeatureFlag.objects.values_list("key", "enabled"))
        cache.set(CACHE_KEY, data, CACHE_SECONDS)
    return data


def raw_value(key):
    """مقدار خود قابلیت، بدون نگاه به والد و وابستگی."""
    stored = _stored()
    if key in stored:
        return stored[key]
    configured = getattr(settings, "FEATURE_FLAGS", {})
    if key in configured:
        return bool(configured[key])
    flag = _registry.get(key)
    return flag.default if flag else False


def blockers(key, _seen=None):
    """فهرست قابلیت‌هایی که باعث خاموش بودن key هستند (خودش، والدها یا وابستگی‌ها)."""
    seen = _seen if _seen is not None else set()
    if key in seen:
        return []
    seen.add(key)
    flag = _registry.get(key)
    result = [] if raw_value(key) else [key]
    parent = flag.parent if flag else (key.rsplit(".", 1)[0] if "." in key else None)
    if parent and parent in _registry:
        result += blockers(parent, seen)
    for req in flag.requires if flag else ():
        result += blockers(req, seen)
    return list(dict.fromkeys(result))


def is_enabled(key):
    return not blockers(key)


def set_flag(key, enabled, user, note):
    """تغییر کنترل‌شده با ثبت علت در گزارش تغییرات."""
    from apps.audit import log
    from apps.audit.models import AuditLog

    from .models import FeatureFlag

    flag = _registry.get(key)
    if flag is None:
        raise ValidationError("قابلیت ناشناخته است.")
    if flag.critical and not enabled:
        raise ValidationError("این قابلیت جزء هسته است و از اینجا خاموش نمی‌شود.")
    if not (note or "").strip():
        raise ValidationError("علت تغییر را بنویسید.")
    obj, _ = FeatureFlag.objects.update_or_create(
        key=key, defaults={"enabled": bool(enabled), "note": note.strip()[:300], "updated_by": user}
    )
    cache.delete(CACHE_KEY)
    log.record(AuditLog.Action.STATUS, obj, message=f"{'روشن' if enabled else 'خاموش'} کردن {key}: {note}"[:500], user=user)
    return obj


def reset_cache():
    cache.delete(CACHE_KEY)


def status():
    """وضعیت همه قابلیت‌ها برای صفحه تنظیمات و health check."""
    rows = []
    for key, flag in registry().items():
        block = blockers(key)
        rows.append({
            "key": key, "label": flag.label, "description": flag.description, "own": raw_value(key),
            "effective": not block, "blocked_by": [b for b in block if b != key], "critical": flag.critical,
            "depth": key.count("."), "dependents": [k for k in _registry if k != key and _depends_on(k, key)],
        })
    return rows


def _depends_on(key, other):
    flag = _registry.get(key)
    if flag is None:
        return False
    if flag.parent == other or other in flag.requires:
        return True
    return any(_depends_on(nxt, other) for nxt in ([flag.parent] if flag.parent else []) + list(flag.requires))

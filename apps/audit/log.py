"""ثبت خودکار تغییرات مدل‌ها و رویدادها در AuditLog."""

import datetime
import decimal

from django.db.models.fields.files import FieldFile
from django.db.models.signals import post_delete, post_save, pre_save

from .context import get_actor
from .models import AuditLog

SKIP_FIELDS = {"password", "last_login", "updated_at"}
_tracked = set()


def _value(value):
    if isinstance(value, (datetime.date, datetime.datetime, datetime.time, decimal.Decimal)):
        return str(value)
    if isinstance(value, FieldFile):
        return value.name or ""
    return value


def _snapshot(instance):
    data = {}
    for field in instance._meta.concrete_fields:
        if field.name in SKIP_FIELDS:
            continue
        data[field.name] = _value(getattr(instance, field.attname))
    return data


def record(action, instance=None, message="", changes=None, user=None):
    actor, ip = get_actor()
    user = user if user is not None else actor
    entry = AuditLog(action=action, ip=ip, message=message[:500], changes=changes or {})
    if user is not None and getattr(user, "is_authenticated", False):
        entry.user = user
    if instance is not None:
        entry.model = instance._meta.label_lower
        entry.object_id = str(instance.pk or "")
        entry.object_repr = str(instance)[:200]
    entry.save()
    return entry


def _pre_save(sender, instance, **kwargs):
    instance._audit_before = None
    if instance.pk:
        old = sender._default_manager.filter(pk=instance.pk).first()
        if old is not None:
            instance._audit_before = _snapshot(old)


def _post_save(sender, instance, created, **kwargs):
    after = _snapshot(instance)
    if created:
        record(AuditLog.Action.CREATE, instance, changes={"after": after})
        return
    before = getattr(instance, "_audit_before", None) or {}
    diff = {k: [before.get(k), v] for k, v in after.items() if before.get(k) != v}
    if diff:
        record(AuditLog.Action.UPDATE, instance, changes=diff)


def _post_delete(sender, instance, **kwargs):
    record(AuditLog.Action.DELETE, instance, changes={"before": _snapshot(instance)})


def track(model):
    """فعال کردن ثبت تغییرات برای یک مدل."""
    if model in _tracked:
        return
    _tracked.add(model)
    uid = f"audit-{model._meta.label_lower}"
    pre_save.connect(_pre_save, sender=model, dispatch_uid=uid + "-pre")
    post_save.connect(_post_save, sender=model, dispatch_uid=uid + "-post")
    post_delete.connect(_post_delete, sender=model, dispatch_uid=uid + "-del")

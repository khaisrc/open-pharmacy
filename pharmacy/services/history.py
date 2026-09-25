"""The change history hook every service write goes through.

`snapshot()` before a write and `record()` after it produce one FieldChange
row per column that differs, in the writer's transaction. There is exactly
one producer of history rows, here, so a model cannot be audited two ways.
"""

import datetime
import decimal
import uuid

from pharmacy import repositories
from pharmacy.models import FieldChange

# Never worth a row: the key is the row's identity, updated_at is what
# changed_at already says, and the cascade flag is safedelete's own.
IGNORED_FIELDS = frozenset({"id", "updated_at", "deleted_by_cascade"})


def snapshot(instance):
    """{column: value} for every concrete column, FKs as their id."""
    return {
        field.attname: _jsonable(getattr(instance, field.attname))
        for field in instance._meta.concrete_fields
        if field.attname not in IGNORED_FIELDS
    }


def record(ctx, instance, before=None):
    """Write a row per column that changed between `before` and now.

    `before=None` is a create: every populated column is recorded with a
    null old value. A soft delete is an update that sets `deleted`.
    """
    after = snapshot(instance)
    if before is None:
        changes = [(field, None, value) for field, value in after.items() if _populated(value)]
    else:
        changes = [
            (field, before.get(field), value)
            for field, value in after.items()
            if before.get(field) != value
        ]
    if not changes:
        return []
    rows = [
        FieldChange(
            model=instance._meta.model_name,
            record_id=instance.pk,
            field=field,
            old_value=old,
            new_value=new,
            # Exactly one: a token names a staff member, a Django credential
            # names a login. The CHECK constraint holds it.
            changed_by_user=None if ctx.staff is not None else ctx.user,
            changed_by_staff=ctx.staff,
            request_path=ctx.path,
            request_method=ctx.method,
            request_id=ctx.request_id,
        )
        for field, old, new in changes
    ]
    return repositories.field_changes.record(rows)


def _populated(value):
    return value is not None and value != ""


def _jsonable(value):
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime.datetime | datetime.date):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    return value

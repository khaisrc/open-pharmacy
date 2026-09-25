"""Column-level change history for every model.

One row per changed column per write: what it was, what it became, who did
it (the login, and the staff member when the request named one), when, and
which request. Append-only and a plain model: an audit row is never edited,
never soft-deleted and never listed through the API. Written only by the
services, in the writer's transaction, so a rolled-back write leaves no
history. Migrations and `seed_local` produce no rows.
"""

import uuid

from django.conf import settings
from django.db import models


class FieldChange(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # The model's `_meta.model_name` and the row's primary key, rather than a
    # generic FK: the row may since have been hard-deleted and the history
    # must outlive it.
    model = models.CharField(max_length=64)
    record_id = models.UUIDField()
    field = models.CharField(max_length=64)
    # JSON rather than text so a null stays distinct from the string "null".
    # Null old_value is a create; null new_value is a delete.
    old_value = models.JSONField(null=True)
    new_value = models.JSONField(null=True)
    changed_at = models.DateTimeField(auto_now_add=True)
    # Exactly one of the two: a Logto token names a staff member, a Django
    # session or Basic credential names a login.
    changed_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="field_changes",
    )
    changed_by_staff = models.ForeignKey(
        "pharmacy.Staff",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="field_changes",
    )
    request_path = models.CharField(max_length=512)
    request_method = models.CharField(max_length=8)
    # Shared by every row one request produced, so a PATCH reads as one event.
    request_id = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["changed_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(changed_by_user__isnull=False, changed_by_staff__isnull=True)
                | models.Q(changed_by_user__isnull=True, changed_by_staff__isnull=False),
                name="field_change_one_actor",
            )
        ]
        indexes = [
            models.Index(fields=["model", "record_id", "changed_at"]),
            models.Index(fields=["request_id"]),
        ]

    def __str__(self):
        return f"{self.model}.{self.field} on {self.record_id} at {self.changed_at:%Y-%m-%d %H:%M}"

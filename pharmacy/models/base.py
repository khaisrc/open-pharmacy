import uuid

from django.db import models
from safedelete.models import SOFT_DELETE, SafeDeleteModel


class TimeStampedModel(SafeDeleteModel):
    """UUID primary key, created/updated stamps, and soft delete for every model.

    SOFT_DELETE means delete() stamps `deleted` instead of issuing SQL DELETE:
    `objects` returns live rows, `all_objects` returns everything, undelete()
    restores. SOFT_DELETE_CASCADE is deliberately not used -- a prescription
    written by a prescriber who has since left is still a real prescription.
    """

    _safedelete_policy = SOFT_DELETE

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ["-created_at"]

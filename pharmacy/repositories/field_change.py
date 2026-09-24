from pharmacy.models import FieldChange

from .base import Repository


class FieldChangeRepository(Repository):
    model = FieldChange

    def all(self):
        # Append-only and never soft-deleted: every row is live.
        return FieldChange.objects.all()

    def for_record(self, instance):
        return self.find(model=instance._meta.model_name, record_id=instance.pk)

    def record(self, rows):
        return FieldChange.objects.bulk_create(rows)

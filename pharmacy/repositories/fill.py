from django.db.models import Max

from pharmacy.models import FILL_IN_FLIGHT_STATUSES, Fill

from .base import Repository


class FillRepository(Repository):
    model = Fill

    def all(self):
        return Fill.objects.select_related(
            "prescription",
            "pharmacy_location",
            "pharmacy_location__pharmacy",
            "pharmacy_location__address",
            "operator",
            "verified_by",
        )

    def in_flight(self, prescription):
        """The prescription's pending or verified fill, or None."""
        return self.find(prescription=prescription, status__in=FILL_IN_FLIGHT_STATUSES).first()

    def next_sequence(self, prescription):
        # all_objects: a soft-deleted fill keeps its number, and the unique
        # constraint on (prescription, sequence) is unconditional.
        highest = Fill.all_objects.filter(prescription=prescription).aggregate(Max("sequence"))
        return (highest["sequence__max"] or 0) + 1

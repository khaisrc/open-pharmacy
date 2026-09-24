from pharmacy.models import Prescription

from .base import Repository


class PrescriptionRepository(Repository):
    model = Prescription

    def all(self):
        # Without select_related, nested read serializers turn one list request
        # into 3N queries. The fills are prefetched because every quantity the
        # representation carries is derived from them: one query per page
        # rather than one per row.
        return Prescription.objects.select_related(
            "patient",
            "prescriber",
            "medication",
            "patient__address",
            "prescriber__address",
            "pharmacy_location",
            "pharmacy_location__pharmacy",
            "pharmacy_location__address",
        ).prefetch_related("fills")

    def get_locked(self, pk):
        # Without the prefetch: a locked row is about to be written, and a
        # fill list read before the write would answer the totals with stale
        # numbers. The model re-reads the fills when it needs them.
        return self.all().prefetch_related(None).select_for_update(of=("self",)).get(pk=pk)

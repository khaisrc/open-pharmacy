from django.db.models import Count, Q

from pharmacy.models import Pharmacy

from .base import Repository


class PharmacyRepository(Repository):
    model = Pharmacy

    def all(self):
        # Annotated, not stored: the count must exclude soft-deleted locations.
        # annotate() adds a GROUP BY, which makes QuerySet.ordered False even
        # though Meta.ordering still applies -- order_by keeps DRF pagination
        # from warning about an unordered list.
        return (
            Pharmacy.objects.select_related("rx_counter")
            .annotate(location_count=Count("locations", filter=Q(locations__deleted__isnull=True)))
            .order_by("-created_at")
        )

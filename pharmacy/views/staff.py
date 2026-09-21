from drf_spectacular.utils import extend_schema_view

from pharmacy.filters import StaffFilter
from pharmacy.models import Staff
from pharmacy.serializers import StaffSerializer

from .base import PharmacyScopedViewSet
from .tags import PHARMACY_ID, tagged


@extend_schema_view(**tagged("Staff", parameters=[PHARMACY_ID]))
class StaffViewSet(PharmacyScopedViewSet):
    """Nested under the employing pharmacy only: there is no cross-pharmacy
    staff list, because a person is looked up through their employer or
    their state registration, and the latter is a filter here."""

    queryset = Staff.objects.select_related("pharmacy")
    serializer_class = StaffSerializer
    filterset_class = StaffFilter
    search_fields = ["first_name", "last_name", "registration_number", "npi"]
    ordering_fields = ["created_at", "last_name"]
    # A location's pharmacist-in-charge must stay resolvable; revoke the
    # designation first.
    protected_relations = (("pic_of_locations", "pharmacy locations"),)

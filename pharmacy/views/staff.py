from drf_spectacular.utils import extend_schema_view

from pharmacy import repositories, services
from pharmacy.auth.scopes import Scope, crud_scopes
from pharmacy.filters import StaffFilter
from pharmacy.serializers import StaffSerializer

from .base import PharmacyScopedViewSet
from .tags import PHARMACY_ID, tagged

SCOPES = crud_scopes(Scope.STAFF_READ, Scope.STAFF_WRITE)


@extend_schema_view(**tagged("Staff", parameters=[PHARMACY_ID], scopes=SCOPES))
class StaffViewSet(PharmacyScopedViewSet):
    """Nested under the employing pharmacy only: there is no cross-pharmacy
    staff list, because a person is looked up through their employer or
    their state registration, and the latter is a filter here."""

    repository = repositories.staff
    service = services.staff
    required_scopes = SCOPES
    serializer_class = StaffSerializer
    filterset_class = StaffFilter
    search_fields = ["first_name", "last_name", "registration_number", "npi"]
    ordering_fields = ["created_at", "last_name"]

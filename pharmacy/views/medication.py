from drf_spectacular.utils import extend_schema_view

from pharmacy import repositories, services
from pharmacy.auth.scopes import Scope, crud_scopes
from pharmacy.filters import MedicationFilter
from pharmacy.serializers import MedicationSerializer

from .base import SoftDeleteModelViewSet
from .tags import tagged

SCOPES = crud_scopes(Scope.MEDICATIONS_READ, Scope.MEDICATIONS_WRITE)


@extend_schema_view(**tagged("Medications", scopes=SCOPES))
class MedicationViewSet(SoftDeleteModelViewSet):
    repository = repositories.medications
    service = services.medications
    required_scopes = SCOPES
    serializer_class = MedicationSerializer
    filterset_class = MedicationFilter
    search_fields = ["name", "ndc"]
    ordering_fields = ["created_at", "name"]

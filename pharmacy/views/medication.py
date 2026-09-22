from drf_spectacular.utils import extend_schema_view

from pharmacy import repositories, services
from pharmacy.filters import MedicationFilter
from pharmacy.serializers import MedicationSerializer

from .base import SoftDeleteModelViewSet
from .tags import tagged


@extend_schema_view(**tagged("Medications"))
class MedicationViewSet(SoftDeleteModelViewSet):
    repository = repositories.medications
    service = services.medications
    serializer_class = MedicationSerializer
    filterset_class = MedicationFilter
    search_fields = ["name", "ndc"]
    ordering_fields = ["created_at", "name"]

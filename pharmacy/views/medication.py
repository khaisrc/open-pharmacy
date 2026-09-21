from drf_spectacular.utils import extend_schema_view

from pharmacy.filters import MedicationFilter
from pharmacy.models import Medication
from pharmacy.serializers import MedicationSerializer

from .base import SoftDeleteModelViewSet
from .tags import tagged


@extend_schema_view(**tagged("Medications"))
class MedicationViewSet(SoftDeleteModelViewSet):
    queryset = Medication.objects.all()
    serializer_class = MedicationSerializer
    filterset_class = MedicationFilter
    search_fields = ["name", "ndc"]
    ordering_fields = ["created_at", "name"]
    protected_relations = (("prescriptions", "prescriptions"),)

from drf_spectacular.utils import extend_schema_view

from pharmacy.filters import AddressFilter
from pharmacy.models import Address
from pharmacy.serializers import AddressSerializer

from .base import SoftDeleteModelViewSet
from .tags import tagged


@extend_schema_view(**tagged("Addresses"))
class AddressViewSet(SoftDeleteModelViewSet):
    queryset = Address.objects.all()
    serializer_class = AddressSerializer
    filterset_class = AddressFilter
    search_fields = ["line1", "city", "postal_code"]
    ordering_fields = ["created_at", "state", "city"]
    protected_relations = (
        ("patients", "patients"),
        ("prescribers", "prescribers"),
        ("pharmacy_locations", "pharmacy locations"),
    )

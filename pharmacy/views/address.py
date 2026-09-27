from drf_spectacular.utils import extend_schema_view

from pharmacy import repositories, services
from pharmacy.auth.scopes import Scope, crud_scopes
from pharmacy.filters import AddressFilter
from pharmacy.serializers import AddressSerializer

from .base import SoftDeleteModelViewSet
from .tags import tagged

SCOPES = crud_scopes(Scope.ADDRESSES_READ, Scope.ADDRESSES_WRITE)


@extend_schema_view(**tagged("Addresses", scopes=SCOPES))
class AddressViewSet(SoftDeleteModelViewSet):
    repository = repositories.addresses
    service = services.addresses
    required_scopes = SCOPES
    serializer_class = AddressSerializer
    filterset_class = AddressFilter
    search_fields = ["line1", "city", "postal_code"]
    ordering_fields = ["created_at", "state", "city"]

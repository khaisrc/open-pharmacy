from django.contrib import admin

from pharmacy import repositories, services
from pharmacy.models import Address

from .base import ServiceModelAdmin


@admin.register(Address)
class AddressAdmin(ServiceModelAdmin):
    repository = repositories.addresses
    service = services.addresses
    list_display = ["line1", "line2", "city", "state", "postal_code"]
    list_filter = ["state"]
    search_fields = ["line1", "city", "postal_code"]
    readonly_fields = ["id", "created_at", "updated_at"]

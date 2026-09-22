from django.contrib import admin

from pharmacy import repositories, services
from pharmacy.models import PharmacyLocation

from .base import ServiceModelAdmin


@admin.register(PharmacyLocation)
class PharmacyLocationAdmin(ServiceModelAdmin):
    repository = repositories.pharmacy_locations
    service = services.pharmacy_locations
    list_display = ["__str__", "pharmacy", "ncpdp_id", "location_type", "is_24_hour"]
    list_filter = ["location_type", "is_24_hour", "pharmacy"]
    search_fields = ["store_number", "ncpdp_id", "npi", "pharmacy__name"]
    autocomplete_fields = ["pharmacy", "address", "pharmacist_in_charge"]
    readonly_fields = ["id", "created_at", "updated_at"]

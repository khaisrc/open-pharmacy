from django.contrib import admin

from pharmacy import repositories, services
from pharmacy.models import Pharmacy

from .base import ServiceModelAdmin


@admin.register(Pharmacy)
class PharmacyAdmin(ServiceModelAdmin):
    repository = repositories.pharmacies
    service = services.pharmacies
    list_display = ["name", "legal_name", "phone_number", "location_count", "rx_counter_state"]
    search_fields = ["name", "legal_name"]
    readonly_fields = ["id", "created_at", "updated_at"]

    @admin.display(description="Locations")
    def location_count(self, obj):
        return obj.location_count

    @admin.display(description="Rx counter")
    def rx_counter_state(self, obj):
        counter = getattr(obj, "rx_counter", None)
        if counter is None or counter.last_issued is None:
            return "not onboarded"
        return f"last issued {counter.last_issued}"

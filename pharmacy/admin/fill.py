from django.contrib import admin

from pharmacy import repositories, services
from pharmacy.models import Fill

from .base import ServiceModelAdmin


@admin.register(Fill)
class FillAdmin(ServiceModelAdmin):
    """A fill is started through the API, by a staff member the request
    names, and moved through its statuses by the API's actions; neither has
    a login to attribute it to here. What the admin can do is what PATCH
    can: edit the quantity and days' supply of a pending fill. The service
    refuses anything else."""

    repository = repositories.fills
    service = services.fills
    list_display = [
        "__str__",
        "rx_number",
        "status",
        "quantity_dispensed",
        "is_partial",
        "pharmacy_location",
        "operator",
        "verified_by",
        "dispensed_at",
    ]
    list_filter = ["status", "pharmacy_location"]
    search_fields = ["prescription__rx_number", "prescription__patient__last_name"]
    fields = [
        "prescription",
        "sequence",
        "status",
        "rx_number",
        "pharmacy_location",
        "operator",
        "verified_by",
        "quantity_dispensed",
        "is_partial",
        "days_supply",
        "dispensed_at",
        "created_at",
        "updated_at",
    ]
    readonly_fields = [
        "prescription",
        "sequence",
        "status",
        "rx_number",
        "pharmacy_location",
        "operator",
        "verified_by",
        "is_partial",
        "dispensed_at",
        "created_at",
        "updated_at",
    ]

    @admin.display(description="Rx number")
    def rx_number(self, obj):
        return obj.prescription.rx_number

    @admin.display(boolean=True)
    def is_partial(self, obj):
        return obj.is_partial

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

from django.contrib import admin

from pharmacy import repositories, services
from pharmacy.models import Prescriber

from .base import ServiceModelAdmin


@admin.register(Prescriber)
class PrescriberAdmin(ServiceModelAdmin):
    repository = repositories.prescribers
    service = services.prescribers
    list_display = ["last_name", "first_name", "practitioner_type", "npi"]
    list_filter = ["practitioner_type"]
    search_fields = ["first_name", "last_name", "npi"]
    autocomplete_fields = ["address"]
    readonly_fields = ["id", "created_at", "updated_at"]

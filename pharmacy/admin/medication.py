from django.contrib import admin

from pharmacy import repositories, services
from pharmacy.models import Medication

from .base import ServiceModelAdmin


@admin.register(Medication)
class MedicationAdmin(ServiceModelAdmin):
    repository = repositories.medications
    service = services.medications
    list_display = ["name", "formatted_ndc", "strength", "form"]
    list_filter = ["form"]
    search_fields = ["name", "ndc"]
    readonly_fields = ["id", "formatted_ndc", "created_at", "updated_at"]

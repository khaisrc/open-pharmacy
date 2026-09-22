from django.contrib import admin

from pharmacy import repositories, services
from pharmacy.models import Staff

from .base import ServiceModelAdmin


@admin.register(Staff)
class StaffAdmin(ServiceModelAdmin):
    repository = repositories.staff
    service = services.staff
    list_display = [
        "last_name",
        "first_name",
        "role",
        "pharmacy",
        "registration_state",
        "registration_number",
        "is_active",
    ]
    list_filter = ["role", "is_active", "registration_state", "pharmacy"]
    search_fields = ["first_name", "last_name", "registration_number", "npi"]
    autocomplete_fields = ["pharmacy"]
    readonly_fields = ["id", "created_at", "updated_at"]

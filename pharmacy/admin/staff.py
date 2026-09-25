from django.contrib import admin

from pharmacy import repositories, services
from pharmacy.models import Staff

from .base import ServiceModelAdmin


@admin.register(Staff)
class StaffAdmin(ServiceModelAdmin):
    """A row that came from a Logto token (`external_user_id` set) is that
    identity's record: everything but `is_active` is read-only here, and the
    service refuses the rest. A hand-typed row is the directory entry it
    always was."""

    repository = repositories.staff
    service = services.staff
    list_display = [
        "last_name",
        "first_name",
        "email",
        "role",
        "pharmacy",
        "registration_state",
        "registration_number",
        "is_active",
        "source",
    ]
    list_filter = ["role", "is_active", "registration_state", "pharmacy"]
    search_fields = ["first_name", "last_name", "email", "external_user_id", "registration_number"]
    autocomplete_fields = ["pharmacy"]
    readonly_fields = ["id", "external_user_id", "email", "roles", "created_at", "updated_at"]

    @admin.display(description="Source")
    def source(self, obj):
        return "logto" if obj.is_external else "manual"

    def get_readonly_fields(self, request, obj=None):
        fields = list(self.readonly_fields)
        if obj is not None and obj.is_external:
            fields += [f.name for f in Staff._meta.concrete_fields if f.name != "is_active"]
        return fields

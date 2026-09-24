from django.contrib import admin

from pharmacy import repositories
from pharmacy.models import FieldChange

from .base import ReadOnlyAdminMixin, ServiceModelAdmin


@admin.register(FieldChange)
class FieldChangeAdmin(ReadOnlyAdminMixin, ServiceModelAdmin):
    """The change history, read only: it is produced by the services and
    never edited. No service, because nothing here writes."""

    repository = repositories.field_changes
    list_display = [
        "changed_at",
        "model",
        "record_id",
        "field",
        "old_value",
        "new_value",
        "changed_by_user",
        "changed_by_staff",
        "request_method",
        "request_path",
    ]
    list_filter = ["model", "request_method", "changed_by_user"]
    search_fields = ["record_id", "field", "request_id", "request_path"]
    ordering = ["-changed_at"]
    date_hierarchy = "changed_at"

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.select_related("changed_by_user", "changed_by_staff")

    def has_change_permission(self, request, obj=None):
        # View only; the change form is how the admin shows a row.
        return False

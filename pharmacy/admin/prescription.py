from django import forms
from django.contrib import admin

from pharmacy import repositories, services
from pharmacy.models import ALLOWED_TRANSITIONS, Fill, Prescription

from .base import ServiceModelAdmin


class PrescriptionForm(forms.ModelForm):
    """The status field offers only the transitions PATCH would: the ones
    in ALLOWED_TRANSITIONS from the current status. Completed, cancelled
    and expired are reached through the fill workflow and cancel/, never
    typed in. No Meta: the ModelAdmin supplies the model and the fields."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "status" in self.fields and self.instance.pk:
            current = self.instance.status
            reachable = {current, *ALLOWED_TRANSITIONS.get(current, set())}
            self.fields["status"].choices = [
                (value, label)
                for value, label in self.fields["status"].choices
                if value in reachable
            ]


class FillInline(admin.TabularInline):
    """The dispensing history, read only, in sequence."""

    model = Fill
    fk_name = "prescription"
    extra = 0
    can_delete = False
    fields = [
        "sequence",
        "status",
        "quantity_dispensed",
        "days_supply",
        "pharmacy_location",
        "operator",
        "verified_by",
        "dispensed_at",
    ]
    readonly_fields = fields
    ordering = ["sequence"]

    def has_add_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        # The inline formset scopes this to the parent itself.
        return repositories.fills.all()


@admin.register(Prescription)
class PrescriptionAdmin(ServiceModelAdmin):
    repository = repositories.prescriptions
    service = services.prescriptions
    form = PrescriptionForm
    inlines = [FillInline]
    list_display = [
        "rx_number",
        "patient",
        "medication",
        "pharmacy_location",
        "status",
        "quantity",
        "fills_authorized",
        "quantity_dispensed",
        "quantity_remaining",
        "written_at",
    ]
    list_filter = ["status", "intake_source", "pharmacy_location__pharmacy"]
    search_fields = ["rx_number", "patient__last_name", "medication__name"]
    autocomplete_fields = ["patient", "prescriber", "medication", "pharmacy_location"]
    fieldsets = [
        (None, {"fields": ["rx_number", "intake_source", "status"]}),
        ("Order", {"fields": ["patient", "prescriber", "medication", "pharmacy_location"]}),
        (
            "Dispensing",
            {
                "fields": [
                    "dose_amount",
                    "quantity",
                    "fills_authorized",
                    "quantity_authorized",
                    "quantity_dispensed",
                    "quantity_in_progress",
                    "quantity_remaining",
                    "fills_used",
                    "fills_remaining",
                    "instructions",
                ]
            },
        ),
        ("Dates", {"fields": ["written_at", "expires_at"]}),
        ("Notes", {"fields": ["notes", "id", "created_at", "updated_at"]}),
    ]
    derived = [
        "quantity_authorized",
        "quantity_dispensed",
        "quantity_in_progress",
        "quantity_remaining",
        "fills_used",
        "fills_remaining",
    ]

    def get_readonly_fields(self, request, obj=None):
        # rx_number is issued by the service on create; the intake source is
        # set then and never changed; a terminal prescription is closed.
        fields = ["rx_number", *self.derived, "id", "created_at", "updated_at"]
        if obj is not None:
            fields.append("intake_source")
            if obj.is_terminal:
                fields += [f.name for f in Prescription._meta.concrete_fields]
        return fields

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            # Nothing has been dispensed against a prescription that does not
            # exist yet; the derived numbers would all read zero.
            return [
                (name, {"fields": [f for f in opts["fields"] if f not in self.derived]})
                for name, opts in self.fieldsets
            ]
        return self.fieldsets

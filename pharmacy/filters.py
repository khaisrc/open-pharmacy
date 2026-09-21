from django_filters import rest_framework as filters

from .models import (
    Address,
    Medication,
    Patient,
    Pharmacy,
    PharmacyLocation,
    Prescriber,
    Prescription,
    Staff,
)


class PrescriptionFilter(filters.FilterSet):
    written_after = filters.IsoDateTimeFilter(field_name="written_at", lookup_expr="gte")
    written_before = filters.IsoDateTimeFilter(field_name="written_at", lookup_expr="lte")
    # Chain-wide view: every prescription routed to any store of one pharmacy.
    pharmacy = filters.UUIDFilter(field_name="pharmacy_location__pharmacy")

    class Meta:
        model = Prescription
        fields = ["patient", "prescriber", "medication", "pharmacy_location", "pharmacy", "status"]


class PatientFilter(filters.FilterSet):
    last_name = filters.CharFilter(lookup_expr="iexact")

    class Meta:
        model = Patient
        fields = ["last_name", "dob", "sms_opt_in", "gender"]


class PrescriberFilter(filters.FilterSet):
    class Meta:
        model = Prescriber
        fields = ["npi", "practitioner_type"]


class MedicationFilter(filters.FilterSet):
    class Meta:
        model = Medication
        fields = ["form"]


class AddressFilter(filters.FilterSet):
    city = filters.CharFilter(lookup_expr="iexact")
    state = filters.CharFilter(lookup_expr="iexact")

    class Meta:
        model = Address
        fields = ["state", "city", "postal_code"]


class PharmacyFilter(filters.FilterSet):
    class Meta:
        model = Pharmacy
        fields = ["name"]


class PharmacyLocationFilter(filters.FilterSet):
    """No `pharmacy` filter: the route under /pharmacies/{id}/ pins it."""

    state = filters.CharFilter(field_name="address__state", lookup_expr="iexact")
    city = filters.CharFilter(field_name="address__city", lookup_expr="iexact")
    postal_code = filters.CharFilter(field_name="address__postal_code")

    class Meta:
        model = PharmacyLocation
        fields = [
            "location_type",
            "is_24_hour",
            "ncpdp_id",
            "state",
            "city",
            "postal_code",
        ]


class StaffFilter(filters.FilterSet):
    registration_state = filters.CharFilter(lookup_expr="iexact")
    registration_number = filters.CharFilter(lookup_expr="iexact")

    class Meta:
        model = Staff
        fields = [
            "role",
            "is_active",
            "registration_state",
            "registration_number",
            "certification_body",
            "npi",
        ]

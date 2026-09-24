"""Every write leaves a column-level trail: old value, new value, who, when,
which request. Nothing here is reachable through the API; the rows are read
from the model."""

import pytest

from pharmacy.models import FieldChange, FillStatus, PrescriptionStatus

from .factories import (
    AddressFactory,
    FillFactory,
    PharmacyLocationFactory,
    PrescriptionFactory,
    make_npi,
)
from .helpers import PRESCRIPTIONS, dispense, fills_url, prescription_body

pytestmark = pytest.mark.django_db


def history(instance, field=None):
    rows = FieldChange.objects.filter(model=instance._meta.model_name, record_id=instance.pk)
    if field:
        rows = rows.filter(field=field)
    return list(rows)


class TestUpdate:
    def test_one_row_per_changed_column(self, api, user):
        rx = PrescriptionFactory(notes="", instructions="daily")
        response = api.patch(
            f"{PRESCRIPTIONS}{rx.pk}/",
            {"notes": "call patient", "instructions": "daily"},
            format="json",
            HTTP_X_REQUEST_ID="req-1",
        )
        assert response.status_code == 200
        rows = history(rx)
        assert [(r.field, r.old_value, r.new_value) for r in rows] == [
            ("notes", "", "call patient")
        ]
        row = rows[0]
        assert row.changed_by_user == user
        assert row.changed_by_staff is None
        assert row.request_path == f"{PRESCRIPTIONS}{rx.pk}/"
        assert row.request_method == "PATCH"
        assert row.request_id == "req-1"

    def test_staff_is_recorded_when_the_header_names_one(self, as_pharmacist, pharmacist):
        rx = PrescriptionFactory()
        as_pharmacist.patch(f"{PRESCRIPTIONS}{rx.pk}/", {"notes": "x"}, format="json")
        assert history(rx, "notes")[0].changed_by_staff == pharmacist

    def test_foreign_keys_are_recorded_as_ids(self, api):
        rx = PrescriptionFactory()
        elsewhere = PharmacyLocationFactory()
        api.patch(
            f"{PRESCRIPTIONS}{rx.pk}/",
            {"pharmacy_location_id": str(elsewhere.pk)},
            format="json",
        )
        (row,) = history(rx, "pharmacy_location_id")
        assert row.old_value == str(rx.pharmacy_location_id)
        assert row.new_value == str(elsewhere.pk)

    def test_a_write_that_changes_nothing_leaves_no_rows(self, api):
        rx = PrescriptionFactory(notes="same")
        api.patch(f"{PRESCRIPTIONS}{rx.pk}/", {"notes": "same"}, format="json")
        assert history(rx) == []

    def test_a_refused_write_leaves_no_rows(self, api):
        rx = PrescriptionFactory(status=PrescriptionStatus.COMPLETED)
        assert (
            api.patch(f"{PRESCRIPTIONS}{rx.pk}/", {"notes": "x"}, format="json").status_code == 409
        )
        assert history(rx) == []


class TestCreateAndDelete:
    def test_create_records_every_populated_column_with_a_null_old_value(
        self, api, patient, prescriber, medication, location
    ):
        response = api.post(
            PRESCRIPTIONS,
            prescription_body(patient, prescriber, medication, location, notes=""),
            format="json",
        )
        assert response.status_code == 201
        rows = {r.field: r for r in FieldChange.objects.filter(record_id=response.data["id"])}
        assert all(r.old_value is None for r in rows.values())
        assert rows["quantity"].new_value == 30
        assert rows["rx_number"].new_value == 1
        assert rows["patient_id"].new_value == str(patient.pk)
        # Blank and null columns are not "set" by a create.
        assert "notes" not in rows
        assert "expires_at" not in rows
        assert "updated_at" not in rows

    def test_delete_records_the_soft_delete(self, api):
        address = AddressFactory()
        assert api.delete(f"/api/v1/addresses/{address.pk}/").status_code == 204
        (row,) = history(address)
        assert row.field == "deleted"
        assert row.old_value is None
        assert row.new_value is not None

    BODIES = {
        "address": {"line1": "1 Main", "city": "X", "state": "NY", "postal_code": "14850"},
        "medication": {"name": "M", "ndc": "00002821501"},
        "patient": {
            "first_name": "A",
            "last_name": "B",
            "dob": "1990-01-01",
            "phone_number": "+15551234567",
        },
        "prescriber": {"first_name": "A", "last_name": "B", "npi": make_npi(77)},
        "pharmacy": {"name": "P"},
        "location": {"ncpdp_id": "1417231", "phone_number": "+15551234567"},
        "staff": {"first_name": "A", "last_name": "B", "role": "technician"},
    }

    @pytest.mark.parametrize("resource", list(BODIES))
    def test_every_resource_create_is_recorded(self, api, pharmacy, address, resource):
        body = dict(self.BODIES[resource])
        url = f"/api/v1/{resource.replace('y', 'ie') if resource == 'pharmacy' else resource}s/"
        if resource == "location":
            url = f"/api/v1/pharmacies/{pharmacy.pk}/locations/"
            body["address_id"] = str(address.pk)
        elif resource == "staff":
            url = f"/api/v1/pharmacies/{pharmacy.pk}/staff/"
        elif resource == "address":
            url = "/api/v1/addresses/"
        response = api.post(url, body, format="json")
        assert response.status_code == 201, response.data
        assert FieldChange.objects.filter(record_id=response.data["id"]).exists()


class TestFillWorkflowHistory:
    def test_each_transition_leaves_its_rows(self, as_pharmacist, pharmacist):
        rx = PrescriptionFactory(fills_authorized=1)
        created, verified, completed = dispense(as_pharmacist, rx)
        fill_rows = FieldChange.objects.filter(model="fill", record_id=created.data["id"])
        by_request = {}
        for row in fill_rows:
            by_request.setdefault(row.request_id, set()).add(row.field)
        assert len(by_request) == 3  # create, verify, complete
        assert {"status", "verified_by_id"} <= by_request[verified.headers["X-Request-ID"]]
        assert {"status", "dispensed_at"} <= by_request[completed.headers["X-Request-ID"]]
        assert all(r.changed_by_staff == pharmacist for r in fill_rows)
        # Completing the last quantity changed the prescription too, in the
        # same request.
        (rx_row,) = FieldChange.objects.filter(model="prescription", record_id=rx.pk)
        assert (rx_row.field, rx_row.old_value, rx_row.new_value) == (
            "status",
            PrescriptionStatus.ACTIVE,
            PrescriptionStatus.COMPLETED,
        )
        assert rx_row.request_id == completed.headers["X-Request-ID"]

    def test_a_refused_transition_leaves_no_rows(self, as_pharmacist, prescription):
        fill = FillFactory(prescription=prescription, completed=True)
        FieldChange.objects.all().delete()
        response = as_pharmacist.post(fills_url(prescription, fill, "verify"))
        assert response.status_code == 409
        assert not FieldChange.objects.exists()

    def test_lazy_expiry_is_recorded_against_the_request_that_found_it(self, api):
        from django.utils import timezone

        now = timezone.now()
        rx = PrescriptionFactory(
            written_at=now - timezone.timedelta(days=30),
            expires_at=now - timezone.timedelta(days=1),
        )
        api.post(f"{PRESCRIPTIONS}{rx.pk}/cancel/")
        (row,) = history(rx, "status")
        assert row.new_value == PrescriptionStatus.EXPIRED
        assert row.request_path.endswith("/cancel/")

    def test_patch_on_a_pending_fill(self, api, fill):
        api.patch(fills_url(fill.prescription, fill), {"quantity_dispensed": 5}, format="json")
        (row,) = history(fill, "quantity_dispensed")
        assert (row.old_value, row.new_value) == (30, 5)
        assert fill.status == FillStatus.PENDING

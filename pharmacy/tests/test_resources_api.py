"""Patients, prescribers, medications, addresses: CRUD, validation, delete guards."""

import pytest

from .factories import (
    AddressFactory,
    MedicationFactory,
    PatientFactory,
    PrescriberFactory,
    PrescriptionFactory,
    make_npi,
)

pytestmark = pytest.mark.django_db


class TestPatients:
    URL = "/api/v1/patients/"

    def test_create_and_read_nested_address(self, api, address):
        response = api.post(
            self.URL,
            {
                "first_name": "Ada",
                "last_name": "Lovelace",
                "dob": "1815-12-10",
                "phone_number": "+15551234567",
                "address_id": str(address.pk),
            },
            format="json",
        )
        assert response.status_code == 201
        assert response.data["address"]["city"] == address.city
        assert response.data["sms_opt_in"] is False  # consent is never defaulted on

    def test_future_dob_is_400(self, api):
        from django.utils import timezone

        tomorrow = timezone.localdate() + timezone.timedelta(days=1)
        response = api.post(
            self.URL,
            {
                "first_name": "Ada",
                "last_name": "Lovelace",
                "dob": str(tomorrow),
                "phone_number": "+15551234567",
            },
            format="json",
        )
        assert response.status_code == 400

    @pytest.mark.parametrize("phone", ["5551234567", "", "not-a-number"])
    def test_bad_phone_is_400(self, api, phone):
        response = api.post(
            self.URL,
            {"first_name": "Ada", "last_name": "L", "dob": "1990-01-01", "phone_number": phone},
            format="json",
        )
        assert response.status_code == 400

    def test_search_by_name(self, api):
        PatientFactory(first_name="Grace", last_name="Hopper")
        PatientFactory(first_name="Ada", last_name="Lovelace")
        body = api.get(f"{self.URL}?search=Hopper").data
        assert body["count"] == 1

    def test_delete_blocked_by_live_prescriptions(self, api):
        rx = PrescriptionFactory()
        assert api.delete(f"{self.URL}{rx.patient.pk}/").status_code == 409

    def test_nested_prescriptions_route(self, api):
        rx = PrescriptionFactory()
        PrescriptionFactory()
        body = api.get(f"{self.URL}{rx.patient.pk}/prescriptions/").data
        assert body["count"] == 1


class TestPrescribers:
    URL = "/api/v1/prescribers/"

    def test_create_with_valid_npi(self, api):
        response = api.post(
            self.URL,
            {
                "first_name": "Alice",
                "last_name": "Chen",
                "practitioner_type": "MD",
                "npi": make_npi(31),
            },
            format="json",
        )
        assert response.status_code == 201

    def test_bad_check_digit_is_400(self, api):
        response = api.post(
            self.URL,
            {"first_name": "A", "last_name": "B", "practitioner_type": "MD", "npi": "1234567890"},
            format="json",
        )
        assert response.status_code == 400

    def test_duplicate_live_npi_is_409(self, api):
        npi = make_npi(32)
        PrescriberFactory(npi=npi)
        response = api.post(
            self.URL,
            {"first_name": "A", "last_name": "B", "practitioner_type": "MD", "npi": npi},
            format="json",
        )
        assert response.status_code == 409
        assert response.data["error"]["details"]["field"] == "npi"

    def test_unknown_practitioner_type_is_400(self, api):
        response = api.post(
            self.URL,
            {
                "first_name": "A",
                "last_name": "B",
                "practitioner_type": "WIZARD",
                "npi": make_npi(33),
            },
            format="json",
        )
        assert response.status_code == 400

    def test_suffix_and_credential_stay_separate(self, api):
        prescriber = PrescriberFactory(suffix="Jr.", practitioner_type="MD")
        body = api.get(f"{self.URL}{prescriber.pk}/").data
        assert body["suffix"] == "Jr."
        assert body["practitioner_type"] == "MD"


class TestMedications:
    URL = "/api/v1/medications/"

    def test_duplicate_ndc_is_409_with_the_existing_id(self, api):
        # 0002-8215-01 is the 4-4-2 form of the same product as 00002821501.
        existing = MedicationFactory(ndc="00002821501")
        response = api.post(self.URL, {"name": "Copy", "ndc": "0002-8215-01"}, format="json")
        assert response.status_code == 409
        assert response.data["error"]["details"]["medication_id"] == str(existing.pk)

    def test_segmented_ndc_normalized_on_create(self, api):
        response = api.post(self.URL, {"name": "Testazole", "ndc": "0002-8215-01"}, format="json")
        assert response.status_code == 201
        assert response.data["ndc"] == "00002821501"
        assert response.data["formatted_ndc"] == "00002-8215-01"

    def test_previously_deleted_ndc_can_be_recreated(self, api):
        medication = MedicationFactory(ndc="00093505601")
        api.delete(f"{self.URL}{medication.pk}/")
        response = api.post(self.URL, {"name": "Fresh", "ndc": "00093505601"}, format="json")
        assert response.status_code == 201

    def test_delete_blocked_by_live_prescriptions(self, api):
        rx = PrescriptionFactory()
        assert api.delete(f"{self.URL}{rx.medication.pk}/").status_code == 409


class TestAddresses:
    URL = "/api/v1/addresses/"

    def test_state_is_uppercased(self, api):
        response = api.post(
            self.URL,
            {"line1": "1 Main St", "city": "Ithaca", "state": "ny", "postal_code": "14850"},
            format="json",
        )
        assert response.status_code == 201
        assert response.data["state"] == "NY"

    @pytest.mark.parametrize("postal", ["1485", "ABCDE", "14850-12"])
    def test_bad_postal_code_is_400(self, api, postal):
        response = api.post(
            self.URL,
            {"line1": "1 Main St", "city": "Ithaca", "state": "NY", "postal_code": postal},
            format="json",
        )
        assert response.status_code == 400

    def test_unknown_state_is_400(self, api):
        response = api.post(
            self.URL,
            {"line1": "1 Main St", "city": "Ithaca", "state": "ZZ", "postal_code": "14850"},
            format="json",
        )
        assert response.status_code == 400

    def test_delete_blocked_by_any_referencing_record(self, api):
        shared = AddressFactory()
        PatientFactory(address=shared)
        response = api.delete(f"{self.URL}{shared.pk}/")
        assert response.status_code == 409
        assert "patients" in response.data["error"]["details"]["referenced_by"]

    def test_one_address_may_be_shared(self, api):
        shared = AddressFactory()
        PrescriberFactory(address=shared)
        PrescriberFactory(address=shared)
        assert api.delete(f"{self.URL}{shared.pk}/").status_code == 409


class TestPagination:
    def test_envelope_and_page_size(self, api):
        PatientFactory.create_batch(7)
        body = api.get("/api/v1/patients/?page_size=3").data
        assert set(body) >= {"count", "next", "previous", "results"}
        assert body["count"] == 7
        assert len(body["results"]) == 3

    def test_page_size_is_capped(self, api):
        PatientFactory.create_batch(3)
        body = api.get("/api/v1/patients/?page_size=5000").data
        assert len(body["results"]) == 3

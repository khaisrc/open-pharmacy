"""A mom-and-pop shop and a chain must behave identically, and a location is
reached only through its pharmacy."""

import pytest

from pharmacy.models import PharmacyLocation

from .factories import (
    AddressFactory,
    PharmacyFactory,
    PharmacyLocationFactory,
    PrescriptionFactory,
    StaffFactory,
    make_npi,
)

pytestmark = pytest.mark.django_db
PHARMACIES = "/api/v1/pharmacies/"
MISSING = "00000000-0000-4000-8000-000000000000"


def locations_url(pharmacy, location=None):
    url = f"{PHARMACIES}{pharmacy.pk}/locations/"
    return f"{url}{location.pk}/" if location else url


def location_body(address, **overrides):
    body = {"address_id": str(address.pk), "ncpdp_id": "1417231", "phone_number": "+15551234567"}
    body.update(overrides)
    return body


class TestBothShapes:
    def test_independent_and_chain_are_the_same_shape(self, api):
        independent = PharmacyFactory(name="Corner Drug")
        PharmacyLocationFactory(pharmacy=independent)

        chain = PharmacyFactory(name="Bigbox Pharmacy")
        PharmacyLocationFactory.create_batch(3, pharmacy=chain)

        counts = {row["name"]: row["location_count"] for row in api.get(PHARMACIES).data["results"]}
        assert counts["Corner Drug"] == 1
        assert counts["Bigbox Pharmacy"] == 3

    def test_an_independent_opens_a_second_store_without_changing_shape(self, api):
        pharmacy = PharmacyFactory()
        PharmacyLocationFactory(pharmacy=pharmacy)
        assert api.get(f"{PHARMACIES}{pharmacy.pk}/").data["location_count"] == 1

        PharmacyLocationFactory(pharmacy=pharmacy)
        assert api.get(f"{PHARMACIES}{pharmacy.pk}/").data["location_count"] == 2

    def test_location_count_ignores_deleted_locations(self, api, pharmacy):
        locations = PharmacyLocationFactory.create_batch(3, pharmacy=pharmacy)
        locations[0].delete()
        assert api.get(f"{PHARMACIES}{pharmacy.pk}/").data["location_count"] == 2


class TestLocationsAreASubResource:
    def test_roster_lists_only_that_chain(self, api):
        mine = PharmacyFactory()
        PharmacyLocationFactory.create_batch(2, pharmacy=mine)
        PharmacyLocationFactory()  # another pharmacy entirely

        body = api.get(locations_url(mine)).data
        assert body["count"] == 2
        assert {row["pharmacy"]["id"] for row in body["results"]} == {str(mine.pk)}

    def test_roster_takes_the_location_filters(self, api):
        mine = PharmacyFactory()
        PharmacyLocationFactory(pharmacy=mine, location_type="retail")
        specialty = PharmacyLocationFactory(pharmacy=mine, location_type="specialty")
        body = api.get(f"{locations_url(mine)}?location_type=specialty").data
        assert [row["id"] for row in body["results"]] == [str(specialty.pk)]

    def test_create_takes_the_pharmacy_from_the_url(self, api, address, pharmacy):
        other = PharmacyFactory()
        response = api.post(
            locations_url(pharmacy),
            location_body(address, pharmacy_id=str(other.pk)),
            format="json",
        )
        assert response.status_code == 201
        assert response.data["pharmacy"] == {"id": str(pharmacy.pk), "name": pharmacy.name}
        assert response.data["pharmacist_in_charge"] is None

    def test_detail_is_not_reachable_through_another_pharmacy(self, api, location):
        other = PharmacyFactory()
        assert api.get(locations_url(other, location)).status_code == 404
        assert api.get(locations_url(location.pharmacy, location)).status_code == 200

    @pytest.mark.parametrize("method", ["get", "post"])
    def test_unknown_pharmacy_is_404(self, api, address, method):
        response = getattr(api, method)(
            f"{PHARMACIES}{MISSING}/locations/", location_body(address), format="json"
        )
        assert response.status_code == 404
        assert response.data["error"]["code"] == "not_found"

    def test_malformed_pharmacy_id_is_the_api_404(self, api):
        response = api.get(f"{PHARMACIES}not-a-uuid/locations/")
        assert response.status_code == 404
        assert response.data["error"]["code"] == "not_found"

    def test_nested_prescriptions_route(self, api):
        rx = PrescriptionFactory()
        PrescriptionFactory()
        location = rx.pharmacy_location
        body = api.get(f"{locations_url(location.pharmacy, location)}prescriptions/").data
        assert body["count"] == 1
        assert body["results"][0]["id"] == str(rx.pk)


class TestLocationIdentifiers:
    def test_duplicate_ncpdp_is_409(self, api, address, pharmacy):
        PharmacyLocationFactory(ncpdp_id="1417231")
        response = api.post(locations_url(pharmacy), location_body(address), format="json")
        assert response.status_code == 409
        assert response.data["error"]["details"]["field"] == "ncpdp_id"

    def test_ncpdp_is_normalized_on_create(self, api, address, pharmacy):
        response = api.post(
            locations_url(pharmacy), location_body(address, ncpdp_id="141-7231"), format="json"
        )
        assert response.status_code == 201
        assert response.data["ncpdp_id"] == "1417231"

    def test_bad_npi_check_digit_is_400(self, api, address, pharmacy):
        response = api.post(
            locations_url(pharmacy), location_body(address, npi="1234567890"), format="json"
        )
        assert response.status_code == 400

    def test_organizational_npi_uses_the_same_validator_as_prescribers(
        self, api, address, pharmacy
    ):
        response = api.post(
            locations_url(pharmacy), location_body(address, npi=make_npi(777)), format="json"
        )
        assert response.status_code == 201

    def test_store_number_collides_within_a_pharmacy_only(self, api, address, pharmacy):
        PharmacyLocationFactory(pharmacy=pharmacy, store_number="41")

        same = api.post(
            locations_url(pharmacy),
            location_body(address, ncpdp_id="1417234", store_number="41"),
            format="json",
        )
        assert same.status_code == 409

        other = api.post(
            locations_url(PharmacyFactory()),
            location_body(address, ncpdp_id="1417235", store_number="41"),
            format="json",
        )
        assert other.status_code == 201


class TestLocationFiltering:
    def test_filter_by_state_within_a_pharmacy(self, api, pharmacy):
        PharmacyLocationFactory(pharmacy=pharmacy, address=AddressFactory(state="NY"))
        PharmacyLocationFactory(pharmacy=pharmacy, address=AddressFactory(state="CA"))
        body = api.get(f"{locations_url(pharmacy)}?state=ny").data
        assert body["count"] == 1
        assert body["results"][0]["address"]["state"] == "NY"

    def test_prescriptions_filterable_by_chain(self, api):
        chain = PharmacyFactory()
        store = PharmacyLocationFactory(pharmacy=chain)
        PrescriptionFactory(pharmacy_location=store)
        PrescriptionFactory()

        body = api.get(f"/api/v1/prescriptions/?pharmacy={chain.pk}").data
        assert body["count"] == 1


class TestDeleteGuards:
    def test_pharmacy_with_live_locations_is_409(self, api, pharmacy):
        PharmacyLocationFactory(pharmacy=pharmacy)
        response = api.delete(f"{PHARMACIES}{pharmacy.pk}/")
        assert response.status_code == 409
        assert "pharmacy locations" in response.data["error"]["details"]["referenced_by"]

    def test_closing_a_chain_is_bottom_up(self, api, pharmacy):
        location = PharmacyLocationFactory(pharmacy=pharmacy)
        assert api.delete(f"{PHARMACIES}{pharmacy.pk}/").status_code == 409
        assert api.delete(locations_url(pharmacy, location)).status_code == 204
        assert api.delete(f"{PHARMACIES}{pharmacy.pk}/").status_code == 204

    def test_location_with_live_prescriptions_is_409(self, api):
        rx = PrescriptionFactory()
        location = rx.pharmacy_location
        response = api.delete(locations_url(location.pharmacy, location))
        assert response.status_code == 409

    def test_location_becomes_deletable_once_prescriptions_go(self, api):
        rx = PrescriptionFactory()
        location = rx.pharmacy_location
        api.delete(f"/api/v1/prescriptions/{rx.pk}/")
        assert api.delete(locations_url(location.pharmacy, location)).status_code == 204
        assert PharmacyLocation.all_objects.filter(pk=location.pk).exists()


class TestLocationNPIUniqueness:
    def test_duplicate_live_npi_is_409(self, api, address, pharmacy):
        npi = make_npi(901)
        PharmacyLocationFactory(npi=npi)
        response = api.post(locations_url(pharmacy), location_body(address, npi=npi), format="json")
        assert response.status_code == 409
        assert response.data["error"]["details"]["field"] == "npi"

    def test_blank_npis_never_collide(self, api, pharmacy):
        PharmacyLocationFactory(pharmacy=pharmacy, npi="")
        PharmacyLocationFactory(pharmacy=pharmacy, npi="")


class TestPharmacistInCharge:
    """The PIC is the board-registered designation for the site: an active
    pharmacist employed by the same pharmacy, checked when designated."""

    def test_set_on_create_reads_back_the_summary(self, api, address, pharmacist):
        response = api.post(
            locations_url(pharmacist.pharmacy),
            location_body(address, pharmacist_in_charge_id=str(pharmacist.pk)),
            format="json",
        )
        assert response.status_code == 201
        assert response.data["pharmacist_in_charge"] == {
            "id": str(pharmacist.pk),
            "first_name": pharmacist.first_name,
            "last_name": pharmacist.last_name,
            "role": "pharmacist",
            "registration_state": "OR",
            "registration_number": pharmacist.registration_number,
        }

    def test_set_and_cleared_by_patch(self, api, pharmacist):
        location = PharmacyLocationFactory(pharmacy=pharmacist.pharmacy)
        url = locations_url(pharmacist.pharmacy, location)

        response = api.patch(url, {"pharmacist_in_charge_id": str(pharmacist.pk)}, format="json")
        assert response.status_code == 200
        assert response.data["pharmacist_in_charge"]["id"] == str(pharmacist.pk)

        response = api.patch(url, {"pharmacist_in_charge_id": None}, format="json")
        assert response.status_code == 200
        assert response.data["pharmacist_in_charge"] is None

    def test_technician_is_refused(self, api, technician):
        location = PharmacyLocationFactory(pharmacy=technician.pharmacy)
        response = api.patch(
            locations_url(technician.pharmacy, location),
            {"pharmacist_in_charge_id": str(technician.pk)},
            format="json",
        )
        assert response.status_code == 400
        assert response.data["error"]["code"] == "validation_error"
        assert "pharmacist_in_charge_id" in response.data["error"]["details"]

    def test_inactive_pharmacist_is_refused(self, api, pharmacy):
        gone = StaffFactory(pharmacy=pharmacy, is_active=False)
        location = PharmacyLocationFactory(pharmacy=pharmacy)
        response = api.patch(
            locations_url(pharmacy, location),
            {"pharmacist_in_charge_id": str(gone.pk)},
            format="json",
        )
        assert response.status_code == 400
        assert "pharmacist_in_charge_id" in response.data["error"]["details"]

    def test_pharmacist_employed_elsewhere_is_refused(self, api, address, pharmacy):
        elsewhere = StaffFactory()
        response = api.post(
            locations_url(pharmacy),
            location_body(address, pharmacist_in_charge_id=str(elsewhere.pk)),
            format="json",
        )
        assert response.status_code == 400
        assert "pharmacist_in_charge_id" in response.data["error"]["details"]

    def test_designation_survives_the_pharmacist_becoming_inactive(self, api, pharmacist):
        """Write-time validation only: nothing revokes a PIC on its own."""
        location = PharmacyLocationFactory(
            pharmacy=pharmacist.pharmacy, pharmacist_in_charge=pharmacist
        )
        api.patch(
            f"/api/v1/pharmacies/{pharmacist.pharmacy.pk}/staff/{pharmacist.pk}/",
            {"is_active": False},
            format="json",
        )
        body = api.get(locations_url(pharmacist.pharmacy, location)).data
        assert body["pharmacist_in_charge"]["id"] == str(pharmacist.pk)

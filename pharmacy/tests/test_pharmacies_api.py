"""A mom-and-pop shop and a chain must behave identically."""

import pytest

from pharmacy.models import PharmacyLocation

from .factories import PharmacyFactory, PharmacyLocationFactory, PrescriptionFactory, make_npi

pytestmark = pytest.mark.django_db
PHARMACIES = "/api/v1/pharmacies/"
LOCATIONS = "/api/v1/pharmacy-locations/"


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

    def test_nested_roster_lists_only_that_chain(self, api):
        mine = PharmacyFactory()
        PharmacyLocationFactory.create_batch(2, pharmacy=mine)
        PharmacyLocationFactory()  # another pharmacy entirely

        body = api.get(f"{PHARMACIES}{mine.pk}/locations/").data
        assert body["count"] == 2
        assert {row["pharmacy"]["id"] for row in body["results"]} == {str(mine.pk)}


class TestLocationIdentifiers:
    def test_duplicate_ncpdp_is_409(self, api, address, pharmacy):
        PharmacyLocationFactory(ncpdp_id="1417231")
        response = api.post(
            LOCATIONS,
            {
                "pharmacy_id": str(pharmacy.pk),
                "address_id": str(address.pk),
                "ncpdp_id": "1417231",
                "phone_number": "+15551234567",
            },
            format="json",
        )
        assert response.status_code == 409
        assert response.data["error"]["details"]["field"] == "ncpdp_id"

    def test_ncpdp_is_normalized_on_create(self, api, address, pharmacy):
        response = api.post(
            LOCATIONS,
            {
                "pharmacy_id": str(pharmacy.pk),
                "address_id": str(address.pk),
                "ncpdp_id": "141-7231",
                "phone_number": "+15551234567",
            },
            format="json",
        )
        assert response.status_code == 201
        assert response.data["ncpdp_id"] == "1417231"

    def test_bad_npi_check_digit_is_400(self, api, address, pharmacy):
        response = api.post(
            LOCATIONS,
            {
                "pharmacy_id": str(pharmacy.pk),
                "address_id": str(address.pk),
                "ncpdp_id": "1417232",
                "npi": "1234567890",
                "phone_number": "+15551234567",
            },
            format="json",
        )
        assert response.status_code == 400

    def test_organizational_npi_uses_the_same_validator_as_prescribers(
        self, api, address, pharmacy
    ):
        response = api.post(
            LOCATIONS,
            {
                "pharmacy_id": str(pharmacy.pk),
                "address_id": str(address.pk),
                "ncpdp_id": "1417233",
                "npi": make_npi(777),
                "phone_number": "+15551234567",
            },
            format="json",
        )
        assert response.status_code == 201

    def test_store_number_collides_within_a_pharmacy_only(self, api, address, pharmacy):
        PharmacyLocationFactory(pharmacy=pharmacy, store_number="41")

        same = api.post(
            LOCATIONS,
            {
                "pharmacy_id": str(pharmacy.pk),
                "address_id": str(address.pk),
                "ncpdp_id": "1417234",
                "store_number": "41",
                "phone_number": "+15551234567",
            },
            format="json",
        )
        assert same.status_code == 409

        other = api.post(
            LOCATIONS,
            {
                "pharmacy_id": str(PharmacyFactory().pk),
                "address_id": str(address.pk),
                "ncpdp_id": "1417235",
                "store_number": "41",
                "phone_number": "+15551234567",
            },
            format="json",
        )
        assert other.status_code == 201


class TestLocationFiltering:
    def test_filter_by_state_cuts_across_pharmacies(self, api):
        from .factories import AddressFactory

        PharmacyLocationFactory(address=AddressFactory(state="NY"))
        PharmacyLocationFactory(address=AddressFactory(state="CA"))
        body = api.get(f"{LOCATIONS}?state=ny").data
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
        assert api.delete(f"{LOCATIONS}{location.pk}/").status_code == 204
        assert api.delete(f"{PHARMACIES}{pharmacy.pk}/").status_code == 204

    def test_location_with_live_prescriptions_is_409(self, api):
        rx = PrescriptionFactory()
        response = api.delete(f"{LOCATIONS}{rx.pharmacy_location.pk}/")
        assert response.status_code == 409

    def test_location_becomes_deletable_once_prescriptions_go(self, api):
        rx = PrescriptionFactory()
        location = rx.pharmacy_location
        api.delete(f"/api/v1/prescriptions/{rx.pk}/")
        assert api.delete(f"{LOCATIONS}{location.pk}/").status_code == 204
        assert PharmacyLocation.all_objects.filter(pk=location.pk).exists()


class TestLocationNPIUniqueness:
    def test_duplicate_live_npi_is_409(self, api, address, pharmacy):
        npi = make_npi(901)
        PharmacyLocationFactory(npi=npi)
        response = api.post(
            LOCATIONS,
            {
                "pharmacy_id": str(pharmacy.pk),
                "address_id": str(address.pk),
                "ncpdp_id": "1417240",
                "npi": npi,
                "phone_number": "+15551234567",
            },
            format="json",
        )
        assert response.status_code == 409
        assert response.data["error"]["details"]["field"] == "npi"

    def test_blank_npis_never_collide(self, api, pharmacy):
        PharmacyLocationFactory(pharmacy=pharmacy, npi="")
        PharmacyLocationFactory(pharmacy=pharmacy, npi="")

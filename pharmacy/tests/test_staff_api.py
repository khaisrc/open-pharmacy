"""Staff: a sub-resource of the employing pharmacy, keyed by state registration."""

import pytest

from pharmacy.models import Staff

from .factories import PharmacyFactory, PharmacyLocationFactory, StaffFactory, make_npi

pytestmark = pytest.mark.django_db

MISSING = "00000000-0000-4000-8000-000000000000"


def staff_url(pharmacy, staff=None):
    url = f"/api/v1/pharmacies/{pharmacy.pk}/staff/"
    return f"{url}{staff.pk}/" if staff else url


def pharmacist_body(**overrides):
    body = {
        "first_name": "Elena",
        "last_name": "Ortiz",
        "role": "pharmacist",
        "registration_state": "NY",
        "registration_number": "RPH.023417",
    }
    body.update(overrides)
    return body


def technician_body(**overrides):
    body = pharmacist_body(
        first_name="Marco", role="technician", registration_state="", registration_number=""
    )
    body.update(overrides)
    return body


class TestCreateAndRead:
    def test_create_reads_back_employer_and_full_name(self, api, pharmacy):
        response = api.post(
            staff_url(pharmacy), pharmacist_body(middle_name="M", suffix="Jr."), format="json"
        )
        assert response.status_code == 201
        assert response.data["pharmacy"] == {"id": str(pharmacy.pk), "name": pharmacy.name}
        assert response.data["full_name"] == "Elena M Ortiz Jr."
        assert response.data["is_active"] is True

    def test_employer_comes_from_the_url_not_the_body(self, api, pharmacy):
        other = PharmacyFactory()
        response = api.post(
            staff_url(pharmacy), pharmacist_body(pharmacy_id=str(other.pk)), format="json"
        )
        assert response.status_code == 201
        assert response.data["pharmacy"]["id"] == str(pharmacy.pk)

    def test_registration_is_normalized(self, api, pharmacy):
        response = api.post(
            staff_url(pharmacy),
            pharmacist_body(registration_state="ny", registration_number=" rph.023417 "),
            format="json",
        )
        assert response.status_code == 201
        assert response.data["registration_state"] == "NY"
        assert response.data["registration_number"] == "RPH.023417"

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("role", "wizard"),
            ("registration_state", "ZZ"),
            ("certification_body", "ACME"),
        ],
    )
    def test_unknown_choice_is_400(self, api, pharmacy, field, value):
        body = pharmacist_body(**{field: value})
        if field == "certification_body":
            body["certification_number"] = "1"
        response = api.post(staff_url(pharmacy), body, format="json")
        assert response.status_code == 400
        assert response.data["error"]["code"] == "validation_error"
        assert field in response.data["error"]["details"]

    def test_role_is_required(self, api, pharmacy):
        body = pharmacist_body()
        del body["role"]
        response = api.post(staff_url(pharmacy), body, format="json")
        assert response.status_code == 400
        assert "role" in response.data["error"]["details"]

    @pytest.mark.parametrize("method", ["get", "post"])
    def test_unknown_pharmacy_is_404_on_the_collection(self, api, method):
        response = getattr(api, method)(
            f"/api/v1/pharmacies/{MISSING}/staff/", pharmacist_body(), format="json"
        )
        assert response.status_code == 404
        assert response.data["error"]["code"] == "not_found"

    def test_deleted_pharmacy_is_404(self, api, pharmacist):
        pharmacist.pharmacy.delete()
        response = api.get(staff_url(pharmacist.pharmacy))
        assert response.status_code == 404

    def test_malformed_pharmacy_id_is_the_api_404(self, api):
        response = api.get("/api/v1/pharmacies/not-a-uuid/staff/")
        assert response.status_code == 404
        assert response.data["error"]["code"] == "not_found"

    def test_not_visible_under_another_pharmacy(self, api, pharmacist):
        other = PharmacyFactory()
        assert api.get(staff_url(other)).data["count"] == 0
        response = api.get(staff_url(other, pharmacist))
        assert response.status_code == 404
        assert api.get(staff_url(pharmacist.pharmacy, pharmacist)).status_code == 200

    def test_soft_delete_hides_but_keeps_the_row(self, api):
        pharmacist = StaffFactory()
        assert api.delete(staff_url(pharmacist.pharmacy, pharmacist)).status_code == 204
        assert api.get(staff_url(pharmacist.pharmacy, pharmacist)).status_code == 404
        assert Staff.all_objects.filter(pk=pharmacist.pk).exists()


class TestRegistration:
    def test_duplicate_live_registration_is_409(self, api, pharmacy):
        existing = StaffFactory(registration_state="NY", registration_number="RPH.023417")
        response = api.post(staff_url(pharmacy), pharmacist_body(), format="json")
        assert response.status_code == 409
        assert response.data["error"]["code"] == "duplicate_registration_number"
        details = response.data["error"]["details"]
        assert details["field"] == "registration_number"
        assert details["registration_number"] == "RPH.023417"
        assert details["registration_state"] == "NY"
        assert details["existing_id"] == str(existing.pk)

    def test_duplicate_detection_ignores_case(self, api, pharmacy):
        StaffFactory(registration_state="NY", registration_number="RPH.023417")
        response = api.post(
            staff_url(pharmacy), pharmacist_body(registration_number="rph.023417"), format="json"
        )
        assert response.status_code == 409

    def test_same_number_in_another_state_is_fine(self, api, pharmacy):
        StaffFactory(registration_state="CA", registration_number="RPH.023417")
        response = api.post(staff_url(pharmacy), pharmacist_body(), format="json")
        assert response.status_code == 201

    def test_deleted_registration_can_be_reused(self, api, pharmacy):
        gone = StaffFactory(registration_state="NY", registration_number="RPH.023417")
        gone.delete()
        response = api.post(staff_url(pharmacy), pharmacist_body(), format="json")
        assert response.status_code == 201

    def test_patch_into_a_collision_is_409(self, api, pharmacy):
        StaffFactory(registration_state="NY", registration_number="RPH.1")
        mine = StaffFactory(pharmacy=pharmacy, registration_state="NY", registration_number="RPH.2")
        response = api.patch(
            staff_url(pharmacy, mine), {"registration_number": "RPH.1"}, format="json"
        )
        assert response.status_code == 409
        assert response.data["error"]["code"] == "duplicate_registration_number"

    @pytest.mark.parametrize("half", ["registration_state", "registration_number"])
    def test_half_a_registration_is_400(self, api, pharmacy, half):
        value = "NY" if half == "registration_state" else "RPT.1"
        response = api.post(staff_url(pharmacy), technician_body(**{half: value}), format="json")
        assert response.status_code == 400
        assert "registration_number" in response.data["error"]["details"]

    @pytest.mark.parametrize("role", ["pharmacist", "certified_technician"])
    def test_registration_is_required_for(self, api, pharmacy, role):
        body = pharmacist_body(
            role=role,
            registration_state="",
            registration_number="",
            certification_body="PTCB",
            certification_number="10021987",
        )
        response = api.post(staff_url(pharmacy), body, format="json")
        assert response.status_code == 400
        assert "registration_number" in response.data["error"]["details"]

    @pytest.mark.parametrize("role", ["technician", "intern"])
    def test_registration_is_optional_for(self, api, pharmacy, role):
        response = api.post(staff_url(pharmacy), technician_body(role=role), format="json")
        assert response.status_code == 201
        assert response.data["registration_number"] == ""

    def test_unregistered_staff_never_collide(self, api, pharmacy):
        assert api.post(staff_url(pharmacy), technician_body(), format="json").status_code == 201
        assert api.post(staff_url(pharmacy), technician_body(), format="json").status_code == 201

    def test_promoting_an_unregistered_technician_needs_a_registration(
        self, api, pharmacy, technician
    ):
        response = api.patch(staff_url(pharmacy, technician), {"role": "pharmacist"}, format="json")
        assert response.status_code == 400
        assert "registration_number" in response.data["error"]["details"]


class TestCertification:
    @pytest.mark.parametrize(
        "half", [{"certification_body": "PTCB"}, {"certification_number": "10021987"}]
    )
    def test_half_a_certification_is_400(self, api, pharmacy, half):
        response = api.post(staff_url(pharmacy), technician_body(**half), format="json")
        assert response.status_code == 400
        assert "certification_number" in response.data["error"]["details"]

    def test_certified_technician_must_hold_one(self, api, pharmacy):
        body = pharmacist_body(role="certified_technician")
        response = api.post(staff_url(pharmacy), body, format="json")
        assert response.status_code == 400
        assert "certification_number" in response.data["error"]["details"]

        body.update(certification_body="NHA", certification_number="A1B2C3")
        assert api.post(staff_url(pharmacy), body, format="json").status_code == 201

    def test_any_role_may_hold_one(self, api, pharmacy):
        body = pharmacist_body(certification_body="PTCB", certification_number="10021987")
        response = api.post(staff_url(pharmacy), body, format="json")
        assert response.status_code == 201
        assert response.data["certification_body"] == "PTCB"


class TestNPI:
    def test_bad_check_digit_is_400(self, api, pharmacy):
        response = api.post(staff_url(pharmacy), pharmacist_body(npi="1234567890"), format="json")
        assert response.status_code == 400

    def test_valid_npi_is_accepted(self, api, pharmacy):
        response = api.post(staff_url(pharmacy), pharmacist_body(npi=make_npi(501)), format="json")
        assert response.status_code == 201

    def test_duplicate_live_npi_is_409(self, api, pharmacy):
        npi = make_npi(502)
        StaffFactory(npi=npi)
        response = api.post(staff_url(pharmacy), pharmacist_body(npi=npi), format="json")
        assert response.status_code == 409
        assert response.data["error"]["code"] == "duplicate_npi"

    def test_blank_npis_never_collide(self, api, pharmacy):
        assert api.post(staff_url(pharmacy), pharmacist_body(), format="json").status_code == 201
        body = pharmacist_body(registration_number="RPH.2", npi="")
        assert api.post(staff_url(pharmacy), body, format="json").status_code == 201


class TestListing:
    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("role=technician", {"tech"}),
            ("is_active=false", {"gone"}),
            ("registration_state=ny", {"rph", "gone"}),
            ("registration_number=rph.000777", {"rph"}),
            ("certification_body=PTCB", {"tech"}),
            ("search=Ortiz", {"rph"}),
        ],
    )
    def test_filters(self, api, pharmacy, query, expected):
        people = {
            "rph": StaffFactory(
                pharmacy=pharmacy,
                last_name="Ortiz",
                registration_state="NY",
                registration_number="RPH.000777",
            ),
            "gone": StaffFactory(pharmacy=pharmacy, registration_state="NY", is_active=False),
            "tech": StaffFactory(
                pharmacy=pharmacy,
                role="technician",
                registration_state="",
                registration_number="",
                certification_body="PTCB",
                certification_number="1",
            ),
        }
        StaffFactory(role="technician", registration_state="", registration_number="")  # elsewhere
        body = api.get(f"{staff_url(pharmacy)}?{query}").data
        assert {row["id"] for row in body["results"]} == {str(people[k].pk) for k in expected}

    def test_filter_by_npi(self, api, pharmacy):
        npi = make_npi(503)
        wanted = StaffFactory(pharmacy=pharmacy, npi=npi)
        StaffFactory(pharmacy=pharmacy)
        body = api.get(f"{staff_url(pharmacy)}?npi={npi}").data
        assert [row["id"] for row in body["results"]] == [str(wanted.pk)]

    def test_ordering_by_last_name(self, api, pharmacy):
        StaffFactory(pharmacy=pharmacy, last_name="Zhou")
        StaffFactory(pharmacy=pharmacy, last_name="Adams")
        body = api.get(f"{staff_url(pharmacy)}?ordering=last_name").data
        assert [row["last_name"] for row in body["results"]] == ["Adams", "Zhou"]


class TestDeleteGuards:
    def test_pharmacist_in_charge_cannot_be_deleted(self, api):
        pharmacist = StaffFactory()
        location = PharmacyLocationFactory(
            pharmacy=pharmacist.pharmacy, pharmacist_in_charge=pharmacist
        )
        response = api.delete(staff_url(pharmacist.pharmacy, pharmacist))
        assert response.status_code == 409
        assert response.data["error"]["code"] == "referenced"
        assert response.data["error"]["details"]["referenced_by"] == {"pharmacy locations": 1}

        location.pharmacist_in_charge = None
        location.save()
        assert api.delete(staff_url(pharmacist.pharmacy, pharmacist)).status_code == 204

    def test_deletable_once_the_location_is_gone(self, api):
        pharmacist = StaffFactory()
        location = PharmacyLocationFactory(
            pharmacy=pharmacist.pharmacy, pharmacist_in_charge=pharmacist
        )
        location.delete()
        assert api.delete(staff_url(pharmacist.pharmacy, pharmacist)).status_code == 204

    def test_pharmacy_with_live_staff_is_409(self, api):
        pharmacist = StaffFactory()
        response = api.delete(f"/api/v1/pharmacies/{pharmacist.pharmacy.pk}/")
        assert response.status_code == 409
        assert response.data["error"]["code"] == "referenced"
        assert "staff" in response.data["error"]["details"]["referenced_by"]

        api.delete(staff_url(pharmacist.pharmacy, pharmacist))
        assert api.delete(f"/api/v1/pharmacies/{pharmacist.pharmacy.pk}/").status_code == 204


class TestIdentityRows:
    """A row that came from a Logto token is that identity's record: the
    staff API shows it, and may change only is_active."""

    def test_reads_back_the_token_identity(self, api, pharmacist):
        body = api.get(staff_url(pharmacist.pharmacy, pharmacist)).data
        assert body["is_external"] is True
        assert body["external_user_id"] == pharmacist.identity.external_user_id
        assert body["email"] == pharmacist.identity.claims["email"]
        assert body["roles"] == ["pharmacist"]

    @pytest.mark.parametrize("method", ["patch", "delete"])
    def test_identity_is_read_only(self, api, pharmacist, method):
        url = staff_url(pharmacist.pharmacy, pharmacist)
        response = (
            api.patch(url, {"last_name": "Changed"}, format="json")
            if method == "patch"
            else api.delete(url)
        )
        assert response.status_code == 409
        assert response.data["error"]["code"] == "identity_managed_externally"
        pharmacist.refresh_from_db()
        assert pharmacist.deleted is None and pharmacist.last_name != "Changed"

    def test_is_active_is_still_local(self, api, pharmacist):
        response = api.patch(
            staff_url(pharmacist.pharmacy, pharmacist), {"is_active": False}, format="json"
        )
        assert response.status_code == 200
        assert response.data["is_active"] is False

    def test_hand_typed_rows_hold_one_role(self, api, pharmacy):
        response = api.post(
            staff_url(pharmacy),
            {"first_name": "A", "last_name": "B", "role": "technician"},
            format="json",
        )
        assert response.status_code == 201
        assert response.data["roles"] == ["technician"]
        assert response.data["is_external"] is False

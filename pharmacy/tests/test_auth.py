"""Bearer tokens: verification, the principal, scopes, and the identity row.

Every token here is minted by the session issuer (conftest) and verified
against its in-memory key set, so none of this needs a running identity
provider; the claims are the identity claims contract.
"""

import base64
import json
import uuid

import pytest
from django.db import connection
from rest_framework.test import APIClient

from pharmacy import services
from pharmacy.auth import authentication
from pharmacy.auth.providers import get_provider
from pharmacy.auth.scopes import ALL_SCOPES, TECHNICIAN_SCOPES, Scope
from pharmacy.auth.verifier import IdentityUnavailable, TokenVerifier, key_set_from
from pharmacy.exceptions import Unauthenticated
from pharmacy.models import FieldChange, Prescription, Staff

from .factories import FillFactory, PrescriptionFactory, StaffFactory
from .helpers import PRESCRIPTIONS, fills_url, prescription_body, race, start_fill
from .tokens import TokenIssuer

pytestmark = pytest.mark.django_db
PATIENTS = "/api/v1/patients/"


def bearer(token):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def error(response):
    return response.data["error"]["code"]


class TestTokenFailures:
    def test_no_credential_is_401(self):
        response = APIClient().get(PATIENTS)
        assert response.status_code == 401
        assert response["WWW-Authenticate"].startswith("Bearer")

    def test_malformed_header_is_401(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer one two")
        response = client.get(PATIENTS)
        assert response.status_code == 401
        assert error(response) == "token_invalid"

    def test_garbage_token_is_401(self):
        response = bearer("not.a.jwt").get(PATIENTS)
        assert response.status_code == 401
        assert error(response) == "token_invalid"

    def test_expired_token_is_401(self, identity):
        response = bearer(identity().token(ttl=-120)).get(PATIENTS)
        assert response.status_code == 401
        assert error(response) == "token_expired"

    @pytest.mark.parametrize(
        "claim,value",
        [("aud", "https://someone-else.example"), ("iss", "http://other/oidc")],
        ids=["wrong audience", "wrong issuer"],
    )
    def test_wrong_audience_or_issuer_is_401(self, identity, claim, value):
        response = bearer(identity().token(**{claim: value})).get(PATIENTS)
        assert response.status_code == 401
        assert error(response) == "token_invalid"

    def test_signed_by_someone_else_is_401(self, identity):
        stranger = TokenIssuer(kid="stranger")
        ident = identity()
        token = stranger.mint(ident.external_user_id, roles=ident.roles, **ident.claims)
        response = bearer(token).get(PATIENTS)
        assert response.status_code == 401
        assert error(response) == "token_invalid"

    def test_same_key_other_kid_is_401(self, issuer, identity):
        ident = identity()
        token = issuer.mint(ident.external_user_id, roles=ident.roles, **ident.claims)
        header, rest = token.split(".", 1)
        tampered = json.loads(base64.urlsafe_b64decode(header + "=="))
        tampered["kid"] = "rotated-away"
        header = base64.urlsafe_b64encode(json.dumps(tampered).encode()).decode().rstrip("=")
        response = bearer(f"{header}.{rest}").get(PATIENTS)
        assert response.status_code == 401

    def test_token_without_email_is_401(self, identity):
        response = bearer(identity().token(email=None)).get(PATIENTS)
        assert response.status_code == 401
        assert error(response) == "token_without_email"
        assert not Staff.objects.exclude(external_user_id="").exists()

    def test_identity_server_down_is_503(self, identity):
        class Down(TokenVerifier):
            def __init__(self):
                pass

            def verify(self, token):
                raise IdentityUnavailable("connection refused")

        original = authentication.get_verifier()
        authentication.set_verifier(Down())
        try:
            response = bearer(identity().token()).get(PATIENTS)
        finally:
            authentication.set_verifier(original)
        assert response.status_code == 503
        assert error(response) == "identity_unavailable"

    def test_key_rotation(self, issuer, identity):
        """A token signed by a key the verifier has not seen is refused;
        once the key set holds it, accepted -- PyJWKClient does the refresh
        against the provider, the in-memory set is swapped here."""
        rotated = TokenIssuer(kid="rotated")
        ident = identity()
        token = rotated.mint(ident.external_user_id, roles=ident.roles, **ident.claims)
        assert bearer(token).get(PATIENTS).status_code == 401
        both = {"keys": issuer.jwks()["keys"] + rotated.jwks()["keys"]}
        authentication.set_verifier(
            TokenVerifier(provider=get_provider(), key_set=key_set_from(both))
        )
        try:
            assert bearer(token).get(PATIENTS).status_code == 200
        finally:
            authentication.set_verifier(issuer.verifier())


class TestScopes:
    @pytest.mark.parametrize(
        "method,path,scope",
        [
            ("get", PATIENTS, Scope.PATIENTS_READ),
            ("post", PATIENTS, Scope.PATIENTS_WRITE),
            ("get", "/api/v1/prescriptions/", Scope.PRESCRIPTIONS_READ),
            ("post", "/api/v1/prescriptions/", Scope.PRESCRIPTIONS_CREATE),
            ("get", "/api/v1/medications/", Scope.MEDICATIONS_READ),
            ("get", "/api/v1/pharmacies/", Scope.PHARMACIES_READ),
            ("get", "/api/v1/addresses/", Scope.ADDRESSES_READ),
            ("get", "/api/v1/prescribers/", Scope.PRESCRIBERS_READ),
        ],
    )
    def test_each_action_needs_its_scope(self, identity, method, path, scope):
        ident = identity()
        without = getattr(bearer(ident.token(scopes=ALL_SCOPES - {scope})), method)
        response = without(path, {}, format="json")
        assert response.status_code == 403, response.data
        assert error(response) == "insufficient_scope"
        assert response.data["error"]["details"]["required"] == [scope]

        with_scope = getattr(bearer(ident.token(scopes={scope})), method)
        assert with_scope(path, {}, format="json").status_code in (200, 400)

    def test_nested_list_needs_the_childs_read_scope(self, identity, patient):
        url = f"{PATIENTS}{patient.pk}/prescriptions/"
        assert bearer(identity().token(scopes={Scope.PATIENTS_READ})).get(url).status_code == 403
        ok = bearer(identity().token(scopes={Scope.PRESCRIPTIONS_READ})).get(url)
        assert ok.status_code == 200

    def test_scoped_sub_resources(self, identity, pharmacy):
        locations = f"/api/v1/pharmacies/{pharmacy.pk}/locations/"
        staff = f"/api/v1/pharmacies/{pharmacy.pk}/staff/"
        client = bearer(identity().token(scopes={Scope.LOCATIONS_READ}))
        assert client.get(locations).status_code == 200
        assert client.get(staff).status_code == 403

    def test_onboarding_is_its_own_grant(self, identity, pharmacy):
        client = bearer(identity().token(scopes=ALL_SCOPES - {Scope.PHARMACIES_ONBOARD}))
        url = f"/api/v1/pharmacies/{pharmacy.pk}/"
        assert client.patch(url, {"name": "Renamed"}, format="json").status_code == 200
        response = client.patch(url, {"rx_number_seed": 5}, format="json")
        assert response.status_code == 403
        assert response.data["error"]["details"]["required"] == ["pharmacies:onboard"]

    def test_health_and_docs_need_nothing(self):
        assert APIClient().get("/api/v1/health/").status_code == 200

    def test_the_api_root_and_options_are_not_refused(self, api, identity):
        for client in (api, bearer(identity().token())):
            assert client.get("/api/v1/").status_code == 200
            assert client.options(PATIENTS).status_code == 200

    def test_a_method_the_route_does_not_serve_is_405(self, api, identity):
        for client in (api, bearer(identity().token())):
            assert client.delete("/api/v1/prescriptions/").status_code == 405

    def test_a_login_holds_every_scope(self, api):
        """Basic and session credentials stay all-access for the transition."""
        assert api.get(PATIENTS).status_code == 200
        assert api.post("/api/v1/pharmacies/", {"name": "P"}, format="json").status_code == 201


class TestFirstSight:
    def test_creates_the_staff_row_with_history(self, identity, pharmacy):
        ident = identity(roles=("pharmacist",), given_name="Elena", family_name="Ortiz")
        assert not Staff.objects.filter(external_user_id=ident.external_user_id).exists()
        response = bearer(ident.token()).get(PATIENTS)
        assert response.status_code == 200
        staff = Staff.objects.get(external_user_id=ident.external_user_id)
        assert (staff.first_name, staff.last_name) == ("Elena", "Ortiz")
        assert staff.email == ident.claims["email"]
        assert staff.role == "pharmacist" and staff.roles == ["pharmacist"]
        assert staff.pharmacy == pharmacy
        assert staff.registration_number == ident.claims["registration_number"]
        rows = FieldChange.objects.filter(model="staff", record_id=staff.pk)
        assert rows.exists()
        assert all(r.changed_by_staff == staff and r.changed_by_user is None for r in rows)
        assert rows.first().request_path == PATIENTS

    def test_second_request_finds_the_row(self, identity):
        ident = identity()
        client = bearer(ident.token())
        client.get(PATIENTS)
        client.get(PATIENTS)
        assert Staff.objects.filter(external_user_id=ident.external_user_id).count() == 1

    @pytest.mark.parametrize(
        "change",
        [
            {"email": "renamed@example.local"},
            {"family_name": "Ortiz-Reyes"},
            {"registration_number": "RPH.999999"},
            {"roles": ["technician"]},
        ],
        ids=["email", "name", "registration", "roles"],
    )
    def test_a_changed_identity_is_refused(self, identity, change):
        ident = identity()
        ident.register()
        overrides = dict(change)
        roles = overrides.pop("roles", None)
        token = (
            ident.issuer.mint(ident.external_user_id, roles=roles, **{**ident.claims, **overrides})
            if roles
            else ident.token(**overrides)
        )
        response = bearer(token).get(PATIENTS)
        assert response.status_code == 401
        assert error(response) == "identity_changed"
        assert response.data["error"]["details"]["fields"]

    def test_a_changed_supervisor_is_not_an_identity_change(self, identity, pharmacist):
        intern = identity(roles=("intern",), supervising_user_id=pharmacist.external_user_id)
        intern.register()
        other = identity(roles=("technician",)).register()
        response = bearer(intern.token(supervising_user_id=other.external_user_id)).get(PATIENTS)
        assert response.status_code == 200

    @pytest.mark.parametrize(
        "roles,claims,code",
        [
            ((), {}, "identity_incomplete"),
            (
                ("pharmacist",),
                {"registration_number": None, "registration_state": None},
                "identity_incomplete",
            ),
            (("technician",), {"pharmacy_id": None}, "identity_incomplete"),
            (
                ("certified_technician",),
                {"certification_body": None, "certification_number": None},
                "identity_incomplete",
            ),
            (
                ("technician",),
                {"pharmacy_id": "00000000-0000-4000-8000-000000000000"},
                "identity_incomplete",
            ),
            (("technician",), {"pharmacy_id": "store-12"}, "identity_incomplete"),
            (("technician",), {"pharmacy_id": " "}, "identity_incomplete"),
            (("intern",), {"supervising_user_id": None}, "supervisor_required"),
        ],
        ids=[
            "no role",
            "pharmacist unregistered",
            "no pharmacy",
            "certified technician uncertified",
            "unknown pharmacy",
            "malformed pharmacy",
            "blank pharmacy",
            "intern alone",
        ],
    )
    def test_claims_that_make_no_valid_staff_row(self, identity, roles, claims, code):
        ident = identity(roles=roles, **claims)
        response = bearer(ident.token()).get(PATIENTS)
        assert response.status_code == 401
        assert error(response) == code
        assert not Staff.objects.filter(external_user_id=ident.external_user_id).exists()

    def test_role_precedence(self, identity):
        ident = identity(roles=("technician", "pharmacist"))
        bearer(ident.token()).get(PATIENTS)
        staff = Staff.objects.get(external_user_id=ident.external_user_id)
        assert staff.role == "pharmacist"
        assert staff.roles == ["pharmacist", "technician"]

    def test_admin_needs_no_pharmacy(self, identity, prescription):
        admin = identity(roles=("admin",), pharmacy=None)
        client = bearer(admin.token())
        created = start_fill(client, prescription)
        assert created.status_code == 201
        staff = Staff.objects.get(external_user_id=admin.external_user_id)
        assert staff.pharmacy is None and staff.role == "admin"
        # Every scope, but not a pharmacist: verifying names one.
        response = client.post(fills_url(prescription, created.data["id"], "verify"))
        assert response.status_code == 403
        assert error(response) == "pharmacist_required"

    @pytest.mark.parametrize(
        "subject",
        [str(uuid.uuid4()), "google-oauth2|" + "1" * 21, "auth0|" + "a" * 24],
        ids=["uuid", "auth0 social", "auth0 database"],
    )
    def test_a_long_subject_registers(self, identity, subject):
        ident = identity()
        ident.external_user_id = subject
        assert bearer(ident.token()).get(PATIENTS).status_code == 200
        assert Staff.objects.filter(external_user_id=subject).exists()

    def test_describe_refuses_a_malformed_pharmacy(self, identity):
        principal = identity(roles=("technician",), pharmacy_id="store-12").principal()
        with pytest.raises(Unauthenticated) as refused:
            services.staff.describe(principal)
        assert refused.value.detail.code == "identity_incomplete"

    def test_a_known_identity_is_one_query(self, identity, django_assert_num_queries):
        ident = identity()
        ident.register()
        with django_assert_num_queries(1):
            services.staff.identify(ident.principal())


class TestSuccession:
    def test_a_successor_retires_the_row_holding_its_registration(self, identity, pharmacy):
        old = identity(roles=("pharmacist",), registration_number="RPH.000123")
        old_row = old.register()
        new = identity(
            roles=("pharmacist",),
            registration_number="RPH.000123",
            email="elena.ortiz-reyes@example.local",
        )
        response = bearer(new.token()).get(PATIENTS)
        assert response.status_code == 200
        old_row.refresh_from_db()
        assert old_row.is_active is False
        new_row = Staff.objects.get(external_user_id=new.external_user_id)
        assert new_row.is_active and new_row.registration_number == "RPH.000123"
        (row,) = FieldChange.objects.filter(record_id=old_row.pk, field="is_active", old_value=True)
        assert row.new_value is False
        assert row.changed_by_staff == new_row
        assert row.request_path == PATIENTS

    def test_a_hand_typed_row_is_retired_the_same_way(self, identity, pharmacy):
        typed = StaffFactory(
            pharmacy=pharmacy, registration_state="NY", registration_number="RPH.000777"
        )
        new = identity(roles=("pharmacist",), registration_number="RPH.000777")
        assert bearer(new.token()).get(PATIENTS).status_code == 200
        typed.refresh_from_db()
        assert typed.is_active is False

    def test_a_successor_keeping_the_email_registers(self, identity):
        old = identity(
            roles=("pharmacist",), registration_number="RPH.900126", email="kept@example.local"
        )
        old_row = old.register()
        new = identity(
            roles=("pharmacist",), registration_number="RPH.900126", email="kept@example.local"
        )
        assert bearer(new.token()).get(PATIENTS).status_code == 200
        old_row.refresh_from_db()
        assert old_row.is_active is False

    def test_a_promoted_technician_is_retired_by_email(self, identity):
        """A technician holds no registration, so only the email says the new
        pharmacist identity is the same person."""
        old = identity(roles=("technician",), email="promoted@example.local")
        old_row = old.register()
        new = identity(roles=("pharmacist",), email="promoted@example.local")
        assert bearer(new.token()).get(PATIENTS).status_code == 200
        old_row.refresh_from_db()
        assert old_row.is_active is False
        new_row = Staff.objects.get(external_user_id=new.external_user_id)
        assert new_row.is_active and new_row.role == "pharmacist"

    def test_the_old_identity_still_cannot_act(self, identity):
        old = identity(roles=("pharmacist",), registration_number="RPH.000124")
        old.register()
        new = identity(roles=("pharmacist",), registration_number="RPH.000124")
        bearer(new.token()).get(PATIENTS)
        # Suffixed and suspended at the provider by the procedure; were it to get a
        # token, the email would no longer match.
        response = bearer(old.token(email="old@example.local-DEACTIVATED")).get(PATIENTS)
        assert response.status_code == 401
        assert error(response) == "identity_changed"


class TestDeactivation:
    """Suspending a user at the provider stops new tokens; `is_active` is
    what stops one already issued, and what keeps a retired predecessor out."""

    def test_an_inactive_row_cannot_act(self, identity):
        ident = identity()
        staff = ident.register()
        staff.is_active = False
        staff.save(update_fields=["is_active"])
        response = bearer(ident.token()).get(PATIENTS)
        assert response.status_code == 401
        assert error(response) == "staff_inactive"

    def test_reactivating_restores_access(self, identity):
        ident = identity()
        staff = ident.register()
        staff.is_active = False
        staff.save(update_fields=["is_active"])
        staff.is_active = True
        staff.save(update_fields=["is_active"])
        assert bearer(ident.token()).get(PATIENTS).status_code == 200

    def test_a_retired_predecessor_cannot_act_with_its_old_token(self, identity):
        old = identity(roles=("pharmacist",), registration_number="RPH.900125")
        old.register()
        new = identity(roles=("pharmacist",), registration_number="RPH.900125")
        assert bearer(new.token()).get(PATIENTS).status_code == 200
        # The old token, email unchanged: nobody suffixed it at the provider.
        response = bearer(old.token()).get(PATIENTS)
        assert response.status_code == 401
        assert error(response) == "staff_inactive"


@pytest.mark.django_db(transaction=True)
class TestFirstSightConcurrency:
    @pytest.fixture(autouse=True)
    def postgres_only(self):
        if connection.vendor != "postgresql":
            pytest.skip("the registration lock is only exercised on PostgreSQL")

    def test_parallel_first_requests_make_one_row(self, identity):
        ident = identity()
        token = ident.token()
        responses = race([lambda: bearer(token).get(PATIENTS)] * 4)
        assert [getattr(r, "status_code", r) for r in responses] == [200] * 4
        assert Staff.objects.filter(external_user_id=ident.external_user_id).count() == 1


class TestTechnicianScopes:
    def test_technician_scopes_are_what_the_table_says(self, identity):
        ident = identity(roles=("technician",))
        client = bearer(ident.token())
        assert client.get(PATIENTS).status_code == 200
        refused = client.post("/api/v1/medications/", {"name": "M", "ndc": "1"}, format="json")
        assert refused.status_code == 403
        assert Scope.MEDICATIONS_WRITE not in TECHNICIAN_SCOPES
        assert Scope.FILLS_VERIFY not in TECHNICIAN_SCOPES

    def test_creates_and_edits_an_unfilled_prescription(
        self, identity, patient, prescriber, medication, location
    ):
        client = bearer(identity(roles=("technician",)).token())
        created = client.post(
            PRESCRIPTIONS,
            prescription_body(patient, prescriber, medication, location),
            format="json",
        )
        assert created.status_code == 201
        url = f"{PRESCRIPTIONS}{created.data['id']}/"
        assert client.patch(url, {"notes": "checked"}, format="json").status_code == 200

    def test_cannot_change_a_prescription_once_a_fill_is_started(self, identity):
        rx = PrescriptionFactory()
        FillFactory(prescription=rx)
        client = bearer(identity(roles=("technician",)).token())
        response = client.patch(f"{PRESCRIPTIONS}{rx.pk}/", {"notes": "x"}, format="json")
        assert response.status_code == 403
        assert error(response) == "insufficient_scope"
        assert response.data["error"]["details"] == {
            "required": ["prescriptions:update"],
            "reason": "a fill has been started",
        }
        assert client.post(f"{PRESCRIPTIONS}{rx.pk}/cancel/").status_code == 403
        assert client.delete(f"{PRESCRIPTIONS}{rx.pk}/").status_code == 403

    def test_a_cancelled_fill_does_not_count(self, identity):
        rx = PrescriptionFactory()
        FillFactory(prescription=rx, status="cancelled")
        client = bearer(identity(roles=("technician",)).token())
        assert (
            client.patch(f"{PRESCRIPTIONS}{rx.pk}/", {"notes": "x"}, format="json").status_code
            == 200
        )

    def test_a_pharmacist_is_not_limited(self, identity):
        rx = PrescriptionFactory()
        FillFactory(prescription=rx, completed=True)
        client = bearer(identity(roles=("pharmacist",)).token())
        assert (
            client.patch(f"{PRESCRIPTIONS}{rx.pk}/", {"notes": "x"}, format="json").status_code
            == 200
        )

    def test_technician_can_fill_but_not_verify(self, identity, prescription):
        client = bearer(identity(roles=("technician",)).token())
        created = start_fill(client, prescription)
        assert created.status_code == 201
        response = client.post(fills_url(prescription, created.data["id"], "verify"))
        assert response.status_code == 403
        assert response.data["error"]["details"]["required"] == ["fills:verify"]


class TestInterns:
    def test_intern_acts_with_a_supervisor_in_the_token(self, identity, pharmacist, prescription):
        intern = identity(roles=("intern",), supervising_user_id=pharmacist.external_user_id)
        client = bearer(intern.token())
        created = start_fill(client, prescription)
        assert created.status_code == 201
        assert created.data["operator"]["id"] == str(
            Staff.objects.get(external_user_id=intern.external_user_id).pk
        )

    def test_an_intern_token_without_a_supervisor_is_refused_every_time(self, identity, pharmacist):
        intern = identity(roles=("intern",), supervising_user_id=pharmacist.external_user_id)
        intern.register()
        response = bearer(intern.token(supervising_user_id=None)).get(PATIENTS)
        assert response.status_code == 401
        assert error(response) == "supervisor_required"


class TestAttribution:
    def test_history_names_the_staff_member(self, identity):
        ident = identity()
        client = bearer(ident.token())
        response = client.post("/api/v1/pharmacies/", {"name": "Token Pharmacy"}, format="json")
        assert response.status_code == 201
        rows = FieldChange.objects.filter(record_id=response.data["id"])
        staff = Staff.objects.get(external_user_id=ident.external_user_id)
        assert all(r.changed_by_staff == staff and r.changed_by_user is None for r in rows)

    def test_history_names_the_login_otherwise(self, api, user):
        response = api.post("/api/v1/pharmacies/", {"name": "Login Pharmacy"}, format="json")
        rows = FieldChange.objects.filter(record_id=response.data["id"])
        assert all(r.changed_by_user == user and r.changed_by_staff is None for r in rows)

    def test_a_fresh_prescription_by_token_is_numbered(
        self, identity, patient, prescriber, medication, location
    ):
        response = bearer(identity().token()).post(
            PRESCRIPTIONS,
            prescription_body(patient, prescriber, medication, location),
            format="json",
        )
        assert response.status_code == 201
        assert Prescription.objects.get(pk=response.data["id"]).rx_number == 1

    def test_throttle_bucket_is_per_identity(self, identity):
        # DRF's UserRateThrottle keys on request.user.pk; a Principal's is
        # its subject, so two identities never share a bucket.
        ident = identity()
        assert ident.principal().pk == ident.external_user_id

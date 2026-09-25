"""The same flow against a running Logto, when one is configured.

Skipped unless LOGTO_LIVE_ENDPOINT is set, together with the credentials
of a machine-to-machine application holding Logto's Management API role
(LOGTO_LIVE_M2M_ID / LOGTO_LIVE_M2M_SECRET), an application with token
exchange enabled (LOGTO_LIVE_APP_ID), and a user to act as
(LOGTO_LIVE_USER_ID). A real token is minted by the subject-token exchange
in plans/auth §0.2, verified against the live JWKS, and sent
through the API. The user's `pharmacy_id` custom data must be a UUID; the
test creates that pharmacy.
"""

import json
import os
import uuid
from urllib import parse, request

import pytest
from rest_framework.test import APIClient

from pharmacy.auth import authentication
from pharmacy.auth.verifier import LogtoTokenVerifier
from pharmacy.models import Staff

from .factories import PharmacyFactory

pytestmark = pytest.mark.django_db

ENDPOINT = os.environ.get("LOGTO_LIVE_ENDPOINT")
NEEDED = ["LOGTO_LIVE_M2M_ID", "LOGTO_LIVE_M2M_SECRET", "LOGTO_LIVE_APP_ID", "LOGTO_LIVE_USER_ID"]


@pytest.fixture
def live(settings):
    if not ENDPOINT:
        pytest.skip("LOGTO_LIVE_ENDPOINT is not set; the live Logto test is off")
    missing = [name for name in NEEDED if not os.environ.get(name)]
    if missing:
        pytest.skip(f"live Logto test needs {', '.join(missing)}")
    settings.LOGTO_ENDPOINT = ENDPOINT
    verifier = LogtoTokenVerifier(endpoint=ENDPOINT, audience=settings.LOGTO_API_RESOURCE)
    previous = authentication.get_verifier()
    authentication.set_verifier(verifier)
    yield settings
    authentication.set_verifier(previous)


def call(path, *, data=None, body=None, token=None):
    """One HTTP call with the standard library: form `data`, JSON `body`."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if body is not None:
        payload, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    elif data is not None:
        payload = parse.urlencode(data).encode()
    else:
        payload = None
    req = request.Request(f"{ENDPOINT}{path}", data=payload, headers=headers)
    with request.urlopen(req, timeout=30) as response:
        return json.load(response)


def management_token():
    return call(
        "/oidc/token",
        data={
            "grant_type": "client_credentials",
            "client_id": os.environ["LOGTO_LIVE_M2M_ID"],
            "client_secret": os.environ["LOGTO_LIVE_M2M_SECRET"],
            "resource": "https://default.logto.app/api",
            "scope": "all",
        },
    )["access_token"]


def user_token(audience):
    mgmt = management_token()
    user_id = os.environ["LOGTO_LIVE_USER_ID"]
    subject = call("/api/subject-tokens", body={"userId": user_id}, token=mgmt)
    exchanged = call(
        "/oidc/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "client_id": os.environ["LOGTO_LIVE_APP_ID"],
            "subject_token": subject["subjectToken"],
            "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
            "resource": audience,
            "scope": "prescriptions:read patients:read",
        },
    )
    user = call(f"/api/users/{user_id}", token=mgmt)
    return exchanged["access_token"], user


def test_a_real_logto_token_reaches_the_api(live):
    token, user = user_token(live.LOGTO_API_RESOURCE)
    PharmacyFactory(id=uuid.UUID(user["customData"]["pharmacy_id"]))
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    response = client.get("/api/v1/patients/")
    assert response.status_code == 200, response.data
    staff = Staff.objects.get(external_user_id=user["id"])
    assert staff.email == user["primaryEmail"]
    assert staff.is_external
    # And a scope the token did not ask for is refused.
    assert client.post("/api/v1/pharmacies/", {"name": "x"}, format="json").status_code == 403

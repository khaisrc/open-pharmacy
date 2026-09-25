"""The local dev issuer: a token the verifier accepts only when local.py
holds the dev key."""

import io

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from rest_framework.test import APIClient

from pharmacy.auth import authentication
from pharmacy.auth.verifier import LogtoTokenVerifier, key_set_from

from .test_settings_and_seed import environment

pytestmark = pytest.mark.django_db


@pytest.fixture
def dev_key():
    out = io.StringIO()
    call_command("mint_token", "--generate-key", stdout=out)
    return out.getvalue().split("\n", 1)[1].strip()


def mint(pharmacy, **options):
    out = io.StringIO()
    call_command(
        "mint_token",
        "--email",
        "dev@example.local",
        "--roles",
        "pharmacist",
        "--pharmacy",
        str(pharmacy.pk),
        "--registration",
        "NY",
        "RPH.000001",
        stdout=out,
        **options,
    )
    return out.getvalue().strip()


def test_refuses_outside_local():
    with environment(DJANGO_ENV="prod"), pytest.raises(CommandError):
        call_command("mint_token", "--generate-key")


def test_refuses_without_a_key(settings):
    settings.LOGTO_DEV_SIGNING_KEY = ""
    with pytest.raises(CommandError):
        call_command("mint_token", "--email", "x@example.local")


def test_a_dev_token_is_accepted_only_with_the_key(settings, issuer, pharmacy, dev_key):
    settings.LOGTO_DEV_SIGNING_KEY = dev_key
    token = mint(pharmacy)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    # The verifier holds Logto's (here: the session's) keys; the dev key is
    # consulted for the dev kid only while the setting is present.
    authentication.set_verifier(LogtoTokenVerifier(key_set=key_set_from(issuer.jwks())))
    try:
        assert client.get("/api/v1/patients/").status_code == 200
        settings.LOGTO_DEV_SIGNING_KEY = ""
        response = client.get("/api/v1/patients/")
        assert response.status_code == 401
        assert response.data["error"]["code"] == "token_invalid"
    finally:
        authentication.set_verifier(issuer.verifier())

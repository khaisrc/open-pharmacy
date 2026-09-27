"""auth_token against the same fake identity provider as seed_auth's tests:
seed it, then ask for a user's token and read it the way the API does."""

import io

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from pharmacy.auth import Principal
from pharmacy.auth.providers.logto import LogtoProvider
from pharmacy.auth.scopes import ROLE_SCOPES, TECHNICIAN_SCOPES, Scope
from pharmacy.auth.verifier import TokenVerifier, key_set_from

from .test_seed_auth import fake_logto, seed  # noqa: F401  (the fixture, by name)
from .test_settings_and_seed import environment

pytestmark = pytest.mark.django_db


def auth_token(state, *args, **env):
    out = io.StringIO()
    with environment(
        DJANGO_ENV="local",
        AUTH_ADMIN_USERNAME="admin",
        AUTH_ADMIN_PASSWORD="Passw0rd!",
        AUTH_INTERNAL_CONSOLE_URL=state.url,
        ADMIN_ENDPOINT="http://localhost:8442",
        **env,
    ):
        call_command("auth_token", *args, stdout=out)
    return out.getvalue().strip()


def read(state, token):
    provider = LogtoProvider(
        issuer="http://localhost:8441/oidc", audience="https://api.open-pharmacy.local"
    )
    verifier = TokenVerifier(provider=provider, key_set=key_set_from(state.issuer.jwks()))
    return Principal.from_claims(provider.claims(verifier.verify(token)))


class TestAuthToken:
    @pytest.mark.parametrize(
        ("username", "role", "scopes"),
        [
            ("elena_ortiz", "pharmacist", ROLE_SCOPES["pharmacist"]),
            ("marco_diaz", "technician", TECHNICIAN_SCOPES),
        ],
    )
    def test_a_seeded_user_gets_their_own_token(self, fake_logto, settings, username, role, scopes):
        seed(fake_logto, settings)
        principal = read(fake_logto, auth_token(fake_logto, username))
        assert principal.role == role
        assert principal.email == f"{username.replace('_', '.')}@open-pharmacy.local"
        assert principal.scopes == {str(s) for s in scopes}

    def test_scopes_narrow_what_is_asked_for(self, fake_logto, settings):
        seed(fake_logto, settings)
        token = auth_token(fake_logto, "elena_ortiz", "--scopes", str(Scope.FILLS_READ))
        assert read(fake_logto, token).scopes == {str(Scope.FILLS_READ)}

    def test_an_unknown_user_is_refused(self, fake_logto, settings):
        seed(fake_logto, settings)
        with pytest.raises(CommandError, match="no user 'nobody'"):
            auth_token(fake_logto, "nobody")

    def test_refuses_outside_local(self):
        with environment(DJANGO_ENV="prod"), pytest.raises(CommandError):
            call_command("auth_token", "elena_ortiz")

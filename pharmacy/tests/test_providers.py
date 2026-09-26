"""The identity provider adapters: where the keys are, and how native claims
become the contract. Network calls are stubbed; the live Logto test is in
providers/."""

import io
import json
from urllib.error import URLError

import pytest

from pharmacy.auth import claims as c
from pharmacy.auth.providers import OIDCProvider, get_provider, set_provider
from pharmacy.auth.providers.logto import LogtoProvider
from pharmacy.auth.providers.oidc import DiscoveryUnavailable
from pharmacy.auth.verifier import IdentityUnavailable, TokenVerifier


class FakeDiscovery:
    """Stands in for urlopen: records the URL asked for and answers with a
    discovery document, or fails."""

    def __init__(self, jwks_uri=None, fail=False):
        self.jwks_uri, self.fail, self.asked = jwks_uri, fail, []

    def __call__(self, url, timeout=None):
        self.asked.append(url)
        if self.fail:
            raise URLError("connection refused")
        return io.BytesIO(json.dumps({"jwks_uri": self.jwks_uri}).encode())


@pytest.fixture
def discovery(monkeypatch):
    def install(**kwargs):
        fake = FakeDiscovery(**kwargs)
        monkeypatch.setattr("pharmacy.auth.providers.oidc.urllib.request.urlopen", fake)
        return fake

    return install


class TestOIDCProvider:
    def test_discovers_the_jwks_under_the_issuer(self, discovery):
        fake = discovery(
            jwks_uri="https://auth.example.com/realms/rx/protocol/openid-connect/certs"
        )
        provider = OIDCProvider(issuer="https://auth.example.com/realms/rx", audience="api")
        assert provider.jwks_uri() == (
            "https://auth.example.com/realms/rx/protocol/openid-connect/certs"
        )
        assert fake.asked == ["https://auth.example.com/realms/rx/.well-known/openid-configuration"]
        provider.jwks_uri()
        assert len(fake.asked) == 1  # cached

    def test_internal_url_rewrites_where_it_fetches_not_what_it_requires(self, discovery):
        """The issuer stays the public one a token carries; discovery and
        the keys are fetched over the internal address."""
        fake = discovery(jwks_uri="http://localhost:8441/oidc/jwks")
        provider = OIDCProvider(
            issuer="http://localhost:8441/oidc", audience="api", internal_url="http://auth:3001"
        )
        assert provider.issuer == "http://localhost:8441/oidc"
        assert provider.jwks_uri() == "http://auth:3001/oidc/jwks"
        assert fake.asked == ["http://auth:3001/oidc/.well-known/openid-configuration"]

    def test_unreachable_discovery_is_reported_not_guessed(self, discovery):
        discovery(fail=True)
        provider = OIDCProvider(issuer="https://auth.example.com", audience="api")
        with pytest.raises(DiscoveryUnavailable):
            provider.jwks_uri()
        with pytest.raises(IdentityUnavailable):
            TokenVerifier(provider=provider).client()

    def test_claims_are_the_identity_mapping(self):
        raw = {c.SUBJECT: "u1", c.EMAIL: "a@example.local", c.ROLES: ["pharmacist"], "extra": 1}
        assert OIDCProvider(issuer="x", audience="y").claims(raw) == raw

    def test_algorithms_never_include_none_or_symmetric(self):
        assert set(OIDCProvider.algorithms) <= {
            "ES256",
            "ES384",
            "ES512",
            "RS256",
            "RS384",
            "RS512",
        }


class TestLogtoProvider:
    def test_falls_back_to_logtos_documented_jwks_path(self, discovery):
        """Logto's keys are at {issuer}/jwks; a discovery outage does not take
        verification down with it."""
        discovery(fail=True)
        provider = LogtoProvider(
            issuer="http://localhost:8441/oidc", audience="api", internal_url="http://auth:3001"
        )
        assert provider.jwks_uri() == "http://auth:3001/oidc/jwks"

    def test_prefers_discovery_when_it_answers(self, discovery):
        discovery(jwks_uri="http://localhost:8441/oidc/jwks")
        provider = LogtoProvider(issuer="http://localhost:8441/oidc", audience="api")
        assert provider.jwks_uri() == "http://localhost:8441/oidc/jwks"

    def test_emits_the_contract_directly(self):
        """Configured with the customizer in docs/identity-providers/logto.md,
        Logto's token is the contract: nothing to map."""
        raw = {c.SUBJECT: "u1", c.SUPERVISING_USER_ID: "u0", c.SCOPE: "fills:read"}
        assert LogtoProvider(issuer="x", audience="y").claims(raw) == raw


class TestConfiguredProvider:
    def test_settings_choose_the_adapter(self, settings):
        settings.AUTH_PROVIDER = "pharmacy.auth.providers.oidc.OIDCProvider"
        settings.AUTH_ISSUER = "https://auth.example.com/realms/rx"
        settings.AUTH_AUDIENCE = "https://api.example.com"
        settings.AUTH_INTERNAL_URL = ""
        set_provider(None)
        try:
            provider = get_provider()
            assert type(provider) is OIDCProvider
            assert (provider.issuer, provider.audience) == (
                "https://auth.example.com/realms/rx",
                "https://api.example.com",
            )
        finally:
            set_provider(None)

    def test_default_is_logto(self):
        set_provider(None)
        try:
            assert type(get_provider()) is LogtoProvider
        finally:
            set_provider(None)

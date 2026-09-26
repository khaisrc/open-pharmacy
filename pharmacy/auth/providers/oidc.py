"""The generic OpenID Connect adapter.

Finds the keys through discovery -- `{issuer}/.well-known/openid-configuration`
-- which every OIDC provider publishes, so no provider-specific path lives
in this service. The document is fetched once per process and on demand;
a provider that cannot be reached raises `DiscoveryUnavailable`, which the
verifier reports as a 503 rather than a 401, because the token was not
judged.
"""

import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit

from .base import IdentityProvider

DISCOVERY_PATH = "/.well-known/openid-configuration"
TIMEOUT_SECONDS = 10


class DiscoveryUnavailable(Exception):
    """The provider's discovery document could not be fetched."""


class OIDCProvider(IdentityProvider):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._jwks_uri = None

    def internal(self, public_url):
        """`public_url` with its origin swapped for the internal one, when
        there is one: discovery and the JWKS are published under the public
        issuer, and fetched over whatever network this service has."""
        if not self.internal_url:
            return public_url
        parts = urlsplit(public_url)
        base = urlsplit(self.internal_url)
        return urlunsplit((base.scheme, base.netloc, parts.path, parts.query, parts.fragment))

    def discovery_url(self):
        return self.internal(f"{self.issuer}{DISCOVERY_PATH}")

    def jwks_uri(self):
        if self._jwks_uri is None:
            self._jwks_uri = self.internal(self.discover()["jwks_uri"])
        return self._jwks_uri

    def discover(self):
        try:
            with urllib.request.urlopen(self.discovery_url(), timeout=TIMEOUT_SECONDS) as response:
                return json.load(response)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            fallback = self.fallback_jwks_uri()
            if fallback:
                return {"jwks_uri": fallback}
            raise DiscoveryUnavailable(str(exc)) from exc

    def fallback_jwks_uri(self):
        """A provider with a fixed, documented JWKS path may name it, so a
        discovery outage does not take verification down with it. None for
        the generic adapter."""
        return None

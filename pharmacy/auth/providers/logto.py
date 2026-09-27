"""Logto, as an OIDC provider.

The one place in this service that knows anything about Logto: its issuer
is `{endpoint}/oidc` and its keys are at `{issuer}/jwks`. Configured with
the access-token JWT customizer in docs/identity-providers/logto.md, it
emits the claims contract directly, so `claims()` is inherited unchanged.
Two facts worth knowing, both verified against a running instance: a fresh
install signs with EC P-384 (`ES384`), and a token is issued even when the
customizer throws -- which is why the API, not the provider, refuses a token
without an email.
"""

from .oidc import OIDCProvider


class LogtoProvider(OIDCProvider):
    def fallback_jwks_uri(self):
        return self.internal(f"{self.base_url}/jwks")

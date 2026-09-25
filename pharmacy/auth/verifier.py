"""Verifying a Logto access token.

Logto signs access tokens with the OIDC key it publishes at
`{ENDPOINT}/oidc/jwks` -- EC P-384 (`ES384`) on a fresh install, RSA
(`RS256`) if rotated to one -- with `iss = {ENDPOINT}/oidc` and `aud` = the
API resource indicator. Nothing else is accepted: the algorithm allow-list
excludes `none` and every symmetric algorithm by construction.
"""

import jwt
from django.conf import settings
from jwt import PyJWKClient, PyJWKClientError, PyJWKSet

ALGORITHMS = ["ES384", "RS256"]
# The kid a locally minted dev token carries.
DEV_KID = "open-pharmacy-dev"
REQUIRED_CLAIMS = ["exp", "iat", "sub", "aud", "iss"]
# Tolerated clock skew between Logto and this service.
LEEWAY_SECONDS = 60


class IdentityUnavailable(Exception):
    """The JWKS could not be fetched: the token was not judged."""


class LogtoTokenVerifier:
    """Decodes and validates a token, returning its claims.

    `key_set` is the seam the tests use: an in-memory `PyJWKSet` instead of
    a fetch. In production the `PyJWKClient` caches the keys and refreshes
    them once on an unknown `kid`, which is what makes a key rotation in
    Logto a non-event here.
    """

    def __init__(self, endpoint=None, audience=None, key_set=None, internal_endpoint=None):
        # The issuer is built from the public endpoint, because that is what
        # Logto writes into a token; the keys are fetched from the internal
        # one when there is one, because that is where this service can
        # reach Logto (the Compose network; a private subnet behind a proxy).
        self.endpoint = (endpoint or settings.LOGTO_ENDPOINT).rstrip("/")
        self.audience = audience or settings.LOGTO_API_RESOURCE
        self.issuer = f"{self.endpoint}/oidc"
        internal = internal_endpoint or getattr(settings, "LOGTO_INTERNAL_ENDPOINT", "")
        self.jwks_uri = f"{(internal or self.endpoint).rstrip('/')}/oidc/jwks"
        self._key_set = key_set
        self._client = None if key_set else PyJWKClient(self.jwks_uri, cache_keys=True)

    def signing_key(self, token):
        header = jwt.get_unverified_header(token)
        if header.get("kid") == DEV_KID and self._dev_public_key() is not None:
            return self._dev_public_key()
        if self._key_set is not None:
            kid = jwt.get_unverified_header(token).get("kid")
            for key in self._key_set.keys:
                if key.key_id == kid:
                    return key.key
            raise jwt.InvalidTokenError("Unknown signing key.")
        try:
            return self._client.get_signing_key_from_jwt(token).key
        except PyJWKClientError as exc:
            # A kid the set does not hold after a refresh is a bad token; a
            # set that could not be fetched at all is Logto being away.
            if "Unable to find a signing key" in str(exc):
                raise jwt.InvalidTokenError(str(exc)) from exc
            raise IdentityUnavailable(str(exc)) from exc

    def verify(self, token):
        """The claims of a valid token; raises `jwt.InvalidTokenError` (or a
        subclass such as `ExpiredSignatureError`) otherwise."""
        return jwt.decode(
            token,
            self.signing_key(token),
            algorithms=ALGORITHMS,
            issuer=self.issuer,
            audience=self.audience,
            leeway=LEEWAY_SECONDS,
            options={"require": REQUIRED_CLAIMS},
        )

    @staticmethod
    def _dev_public_key():
        """The public half of LOGTO_DEV_SIGNING_KEY, when local sets one: the
        key `manage.py mint_token` signs with. Nothing outside local.py reads
        the setting, so this is None everywhere else."""
        pem = getattr(settings, "LOGTO_DEV_SIGNING_KEY", "")
        if not pem:
            return None
        from cryptography.hazmat.primitives import serialization

        return serialization.load_pem_private_key(pem.encode(), password=None).public_key()


def key_set_from(jwks):
    """A `PyJWKSet` from a JWKS dict, for tests and the dev issuer."""
    return PyJWKSet.from_dict(jwks)

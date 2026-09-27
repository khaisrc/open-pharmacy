"""Verifying a bearer token.

The identity provider publishes the keys that sign its tokens; the
configured adapter (`pharmacy.auth.providers`) says where, and which issuer,
audience and algorithms to require. Nothing else is accepted: the algorithm
allow-list is the adapter's and never contains `none` or a symmetric one.
"""

from functools import cache

import jwt
from django.conf import settings
from jwt import PyJWKClient, PyJWKClientError, PyJWKSet

from .providers import get_provider
from .providers.oidc import DiscoveryUnavailable

REQUIRED_CLAIMS = ["exp", "iat", "sub", "aud", "iss"]
# Tolerated clock skew between the provider and this service.
LEEWAY_SECONDS = 60
# The kid a locally minted dev token carries.
DEV_KID = "open-pharmacy-dev"


class IdentityUnavailable(Exception):
    """The keys could not be fetched: the token was not judged."""


class TokenVerifier:
    """Decodes and validates a token, returning its raw claims.

    `key_set` is the seam the tests use: an in-memory `PyJWKSet` instead of a
    fetch. Otherwise a `PyJWKClient` is built lazily from the adapter's JWKS
    URI (which may itself come from discovery, hence lazily) and caches the
    keys, refreshing them once on an unknown `kid` -- what makes a key
    rotation at the provider a non-event here.
    """

    def __init__(self, provider=None, key_set=None):
        self.provider = provider or get_provider()
        self._key_set = key_set
        self._client = None

    @property
    def issuer(self):
        return self.provider.issuer

    @property
    def audience(self):
        return self.provider.audience

    def client(self):
        if self._client is None:
            try:
                self._client = PyJWKClient(self.provider.jwks_uri(), cache_keys=True)
            except DiscoveryUnavailable as exc:
                raise IdentityUnavailable(str(exc)) from exc
        return self._client

    def signing_key(self, token):
        header = jwt.get_unverified_header(token)
        if header.get("kid") == DEV_KID:
            dev_key = _dev_public_key(getattr(settings, "AUTH_DEV_SIGNING_KEY", ""))
            if dev_key is not None:
                return dev_key
        if self._key_set is not None:
            for key in self._key_set.keys:
                if key.key_id == header.get("kid"):
                    return key.key
            raise jwt.InvalidTokenError("Unknown signing key.")
        try:
            return self.client().get_signing_key_from_jwt(token).key
        except PyJWKClientError as exc:
            # A kid the set does not hold after a refresh is a bad token; a
            # set that could not be fetched at all is the provider being away.
            if "Unable to find a signing key" in str(exc):
                raise jwt.InvalidTokenError(str(exc)) from exc
            raise IdentityUnavailable(str(exc)) from exc

    def verify(self, token):
        """The claims of a valid token; raises `jwt.InvalidTokenError` (or a
        subclass such as `ExpiredSignatureError`) otherwise."""
        return jwt.decode(
            token,
            self.signing_key(token),
            algorithms=list(self.provider.algorithms),
            issuer=self.issuer,
            audience=self.audience,
            leeway=LEEWAY_SECONDS,
            options={"require": REQUIRED_CLAIMS},
        )


@cache
def _dev_public_key(pem):
    """The public half of AUTH_DEV_SIGNING_KEY, when local sets one: the key
    `manage.py mint_token` signs with. Nothing outside local.py reads the
    setting, so this is None everywhere else. Cached per PEM, so a changed
    setting is still read."""
    if not pem:
        return None
    from cryptography.hazmat.primitives import serialization

    # .env files hold the PEM on one line with literal \n.
    pem = pem.replace("\\n", "\n")
    return serialization.load_pem_private_key(pem.encode(), password=None).public_key()


def key_set_from(jwks):
    """A `PyJWKSet` from a JWKS dict, for tests and the dev issuer."""
    return PyJWKSet.from_dict(jwks)

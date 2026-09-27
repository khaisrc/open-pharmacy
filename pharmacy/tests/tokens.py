"""Minting access tokens the way the identity provider does, for the tests.

One EC P-384 key per process, published as a JWKS the verifier is handed
in memory (`pharmacy.auth.authentication.set_verifier`), so no test needs a
running provider. The claims are the identity claims contract
(`pharmacy.auth.claims`), so a test token and a real one differ only in who
signed them.
"""

import time
import uuid

import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from django.conf import settings
from jwt.algorithms import ECAlgorithm

from pharmacy.auth import claims as c
from pharmacy.auth.providers import get_provider
from pharmacy.auth.scopes import ALL_SCOPES, scopes_for_roles
from pharmacy.auth.verifier import TokenVerifier, key_set_from

KID = "test-key"


class TokenIssuer:
    def __init__(self, kid=KID):
        self.kid = kid
        self.private_key = ec.generate_private_key(ec.SECP384R1())
        self.issuer = settings.AUTH_ISSUER
        self.audience = settings.AUTH_AUDIENCE

    def jwks(self):
        public = ECAlgorithm.to_jwk(self.private_key.public_key(), as_dict=True)
        return {"keys": [{**public, "kid": self.kid, "alg": "ES384", "use": "sig"}]}

    def verifier(self):
        return TokenVerifier(provider=get_provider(), key_set=key_set_from(self.jwks()))

    def mint(self, external_user_id, *, scopes=None, roles=("pharmacist",), **claims):
        """A token for `external_user_id`. `scopes` defaults to the union of
        the given roles' scopes, like the provider's role grants; pass a list (or
        an empty one) to say exactly which."""
        if scopes is None:
            scopes = scopes_for_roles(roles)
        now = int(time.time())
        payload = {
            "jti": uuid.uuid4().hex,
            "sub": external_user_id,
            "iat": now,
            "exp": now + claims.pop("ttl", 900),
            "iss": self.issuer,
            "aud": self.audience,
            "client_id": "test-ui",
            "scope": " ".join(sorted(str(s) for s in scopes)),
            "roles": list(roles),
            **claims,
        }
        return jwt.encode(payload, self.private_key, algorithm="ES384", headers={"kid": self.kid})


_counter = iter(range(1, 10**9))


class Identity:
    """A user at the identity provider as the tests know one: a subject, the
    contract claims, and the roles that decide its scopes. `bearer()` puts
    its token on a client; `register()` creates its Staff row the way a
    first request would."""

    def __init__(self, issuer, *, roles=("pharmacist",), pharmacy=None, email=None, **claims):
        n = next(_counter)
        self.issuer = issuer
        self.external_user_id = f"user{n:06d}abcdef"
        self.roles = tuple(roles)
        email = email or f"person{n}@example.local"
        given, family = claims.pop("given_name", f"Person{n}"), claims.pop("family_name", "Tester")
        self.claims = {
            c.EMAIL: email,
            "email_verified": True,
            c.NAME: f"{given} {family}",
            c.GIVEN_NAME: given,
            c.FAMILY_NAME: family,
            c.PHARMACY_ID: str(pharmacy.pk) if pharmacy is not None else None,
            **claims,
        }
        if "pharmacist" in self.roles:
            self.claims.setdefault(c.REGISTRATION_STATE, "NY")
            self.claims.setdefault(c.REGISTRATION_NUMBER, f"RPH.{n:06d}")
        if "certified_technician" in self.roles:
            self.claims.setdefault(c.REGISTRATION_STATE, "NY")
            self.claims.setdefault(c.REGISTRATION_NUMBER, f"RPT.{n:06d}")
            self.claims.setdefault(c.CERTIFICATION_BODY, "PTCB")
            self.claims.setdefault(c.CERTIFICATION_NUMBER, f"{n:010d}")

    def token(self, scopes=None, **overrides):
        claims = {**self.claims, **overrides}
        return self.issuer.mint(self.external_user_id, scopes=scopes, roles=self.roles, **claims)

    def principal(self, **overrides):
        from pharmacy.auth import Principal

        claims = {"sub": self.external_user_id, "roles": list(self.roles), **self.claims}
        return Principal.from_claims({**claims, **overrides})

    def register(self):
        from pharmacy import services

        return services.staff.identify(self.principal(), path="/test/register", method="TEST")

    def bearer(self, client, scopes=None, **overrides):
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token(scopes, **overrides)}")
        return client


ALL = ALL_SCOPES

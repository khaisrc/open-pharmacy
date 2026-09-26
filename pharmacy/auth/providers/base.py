from django.conf import settings


class IdentityProvider:
    """What the token verifier needs from an identity provider.

    `issuer` and `audience` are the `iss` and `aud` a token must carry;
    `algorithms` is the signature allow-list (never `none`, never a symmetric
    one); `jwks_uri()` is where the signing keys are published; `claims()`
    turns a token's raw claims into the contract. Subclasses fill these in.
    """

    algorithms = ("ES384", "RS256")

    def __init__(self, issuer, audience, internal_url=None):
        self.issuer = issuer.rstrip("/")
        self.audience = audience
        # Where this service reaches the provider when that is not the
        # public issuer: the Compose network, a private subnet behind a
        # proxy. None means the issuer itself.
        self.internal_url = (internal_url or "").rstrip("/") or None

    @classmethod
    def from_settings(cls):
        return cls(
            issuer=settings.AUTH_ISSUER,
            audience=settings.AUTH_AUDIENCE,
            internal_url=getattr(settings, "AUTH_INTERNAL_URL", ""),
        )

    def jwks_uri(self):
        raise NotImplementedError

    def claims(self, raw):
        """The contract's claims from a token's raw claims. The identity
        mapping by default: a provider configured to emit the contract."""
        return dict(raw)

"""DRF authentication for Logto bearer tokens.

Verifies the token, reads the principal, and resolves the `Staff` row the
subject maps to -- creating it on first sight, refusing it if the identity
no longer matches (`services.staff.identify`). Sits first in
DEFAULT_AUTHENTICATION_CLASSES; a request without a bearer header falls
through to Basic or session authentication, whose Django user the base
viewset turns into a principal of its own.
"""

from dataclasses import replace

import jwt
from rest_framework.authentication import BaseAuthentication, get_authorization_header

from pharmacy import services
from pharmacy.exceptions import IdentityUnavailable, Unauthenticated

from . import verifier as verifier_module
from .principal import Principal

# Replaced by the tests with a verifier holding an in-memory key set.
_verifier = None


def get_verifier():
    global _verifier
    if _verifier is None:
        _verifier = verifier_module.LogtoTokenVerifier()
    return _verifier


def set_verifier(verifier):
    global _verifier
    _verifier = verifier


class LogtoJWTAuthentication(BaseAuthentication):
    keyword = "Bearer"

    def authenticate(self, request):
        header = get_authorization_header(request).split()
        if not header or header[0].lower() != self.keyword.lower().encode():
            return None
        if len(header) != 2:
            raise Unauthenticated("The Authorization header is malformed.", code="token_invalid")
        token = header[1].decode()
        try:
            claims = get_verifier().verify(token)
        except jwt.ExpiredSignatureError as exc:
            raise Unauthenticated("The token has expired.", code="token_expired") from exc
        except jwt.InvalidTokenError as exc:
            raise Unauthenticated(f"The token is invalid: {exc}", code="token_invalid") from exc
        except verifier_module.IdentityUnavailable as exc:
            raise IdentityUnavailable() from exc
        principal = Principal.from_claims(claims)
        if not principal.email:
            raise Unauthenticated(
                "The token carries no email claim; the identity server's JWT customizer "
                "must add it.",
                code="token_without_email",
            )
        staff = services.staff.identify(
            principal,
            path=request.path,
            method=request.method,
            request_id=getattr(request, "request_id", ""),
        )
        return replace(principal, staff=staff), token

    def authenticate_header(self, request):
        return 'Bearer realm="api"'

"""Bearer-token authentication against Logto, and scope-based permissions.

A request carries `Authorization: Bearer <access token>`; the token is
verified against Logto's JWKS (`verifier`), read into a `Principal`
(`principal`), matched to its `Staff` row (`authentication`), and checked
for the scopes the action needs (`permissions`). The scopes themselves are
named once, in `scopes`.
"""

from . import schema  # noqa: F401  (registers the OpenAPI security scheme)
from .authentication import LogtoJWTAuthentication
from .permissions import HasScope
from .principal import Principal
from .scopes import Scope
from .verifier import LogtoTokenVerifier

__all__ = ["HasScope", "LogtoJWTAuthentication", "LogtoTokenVerifier", "Principal", "Scope"]

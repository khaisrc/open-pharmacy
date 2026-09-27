"""Bearer-token authentication and scope-based permissions.

A request carries `Authorization: Bearer <access token>`; the token is
verified against the configured identity provider's keys (`verifier`,
through a provider adapter in `providers`), its claims are mapped to the
contract (`claims`) and read into a `Principal` (`principal`), the subject
is matched to its `Staff` row (`authentication`), and the action's scopes
are checked (`permissions`). The scopes themselves are named once, in
`scopes`. Nothing here knows which provider is behind the adapter.
"""

from . import schema  # noqa: F401  (registers the OpenAPI security scheme)
from .authentication import BearerTokenAuthentication
from .permissions import HasScope
from .principal import Principal, principal_of
from .scopes import Scope
from .verifier import TokenVerifier

__all__ = [
    "BearerTokenAuthentication",
    "HasScope",
    "Principal",
    "Scope",
    "TokenVerifier",
    "principal_of",
]

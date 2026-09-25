"""Swagger grouping, and the parameters the router cannot see.

Tags are declared, never inferred. drf-spectacular derives a tag from the first
path segment when none is given, which would emit a lowercase
`pharmacy-locations` group and give /health/ a stray one of its own.
"""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema

READ_ACTIONS = ["list", "retrieve"]
WRITE_ACTIONS = ["create", "update", "partial_update", "destroy"]
ACTIONS = [*READ_ACTIONS, *WRITE_ACTIONS]

# The parent segment of a sub-resource. Declared because spectacular cannot
# see through a hand-rolled <str:pharmacy_id> to the UUID it really is.
PHARMACY_ID = OpenApiParameter(
    "pharmacy_id", OpenApiTypes.UUID, OpenApiParameter.PATH, description="The owning pharmacy."
)
PRESCRIPTION_ID = OpenApiParameter(
    "prescription_id",
    OpenApiTypes.UUID,
    OpenApiParameter.PATH,
    description="The prescription being filled.",
)


def tagged(tag, *extra_actions, parameters=(), actions=ACTIONS, scopes=None):
    """extend_schema_view kwargs putting every action of a viewset under `tag`.

    A viewset without the full CRUD set names the `actions` it has. With
    `scopes` (the viewset's `required_scopes`), each operation's description
    states the scopes that grant it, so the docs say what a 403 will.
    """
    kwargs = {}
    for action in [*actions, *extra_actions]:
        extra = {}
        required = (scopes or {}).get(action)
        if required:
            extra["description"] = "Requires scope: " + " or ".join(sorted(required)) + "."
        kwargs[action] = extend_schema(tags=[tag], parameters=list(parameters), **extra)
    return kwargs

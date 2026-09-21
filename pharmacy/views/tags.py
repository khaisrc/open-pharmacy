"""Swagger grouping.

Tags are declared, never inferred. drf-spectacular derives a tag from the first
path segment when none is given, which would emit a lowercase
`pharmacy-locations` group and give /health/ a stray one of its own.
"""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema

ACTIONS = ["list", "create", "retrieve", "update", "partial_update", "destroy"]

# The parent segment of a sub-resource. Declared because spectacular cannot
# see through a hand-rolled <str:pharmacy_id> to the UUID it really is.
PHARMACY_ID = OpenApiParameter(
    "pharmacy_id", OpenApiTypes.UUID, OpenApiParameter.PATH, description="The owning pharmacy."
)


def tagged(tag, *extra_actions, parameters=()):
    """extend_schema_view kwargs putting every action of a viewset under `tag`."""
    return {
        action: extend_schema(tags=[tag], parameters=list(parameters))
        for action in [*ACTIONS, *extra_actions]
    }

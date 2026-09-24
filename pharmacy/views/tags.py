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
# The staff member performing a write. Optional everywhere, recorded in the
# change history when present, required where a fill needs a person.
X_STAFF_ID = OpenApiParameter(
    "X-Staff-Id",
    OpenApiTypes.UUID,
    OpenApiParameter.HEADER,
    required=False,
    description=(
        "The staff member performing this write. Must name a live, active staff "
        "member; required to create or verify a fill."
    ),
)


def tagged(tag, *extra_actions, parameters=(), read_actions=READ_ACTIONS, actions=ACTIONS):
    """extend_schema_view kwargs putting every action of a viewset under `tag`.

    Every action that is not a read is a write and carries the X-Staff-Id
    header; `extra_actions` are writes unless named in `read_actions`. A
    viewset without the full CRUD set names the `actions` it has.
    """
    kwargs = {}
    for action in [*actions, *extra_actions]:
        params = list(parameters)
        if action not in read_actions:
            params.append(X_STAFF_ID)
        kwargs[action] = extend_schema(tags=[tag], parameters=params)
    return kwargs

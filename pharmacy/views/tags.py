"""Swagger grouping.

Tags are declared, never inferred. drf-spectacular derives a tag from the first
path segment when none is given, which would emit a lowercase
`pharmacy-locations` group and give /health/ a stray one of its own.
"""

from drf_spectacular.utils import extend_schema

ACTIONS = ["list", "create", "retrieve", "update", "partial_update", "destroy"]


def tagged(tag, *extra_actions):
    """extend_schema_view kwargs putting every action of a viewset under `tag`."""
    return {action: extend_schema(tags=[tag]) for action in [*ACTIONS, *extra_actions]}

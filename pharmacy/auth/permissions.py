from rest_framework.permissions import BasePermission

from pharmacy.exceptions import InsufficientScope

from .principal import principal_of


class HasScope(BasePermission):
    """The action's scopes, declared on the viewset as
    `required_scopes = {"list": {...}, "create": {...}, ...}`.

    A principal holding at least one of an action's scopes passes; a named
    action with no entry fails closed. The refusal is a 403 that names what
    was required, so a client can tell a missing scope from a wrong user.
    """

    def has_permission(self, request, view):
        user = request.user
        if not getattr(user, "is_authenticated", False):
            return False
        action = getattr(view, "action", None)
        if request.method.lower() not in getattr(view, "http_method_names", ()):
            # Let the 405 answer; a method the view does not serve is not a
            # permission question.
            return True
        if action is None or action == "metadata":
            # Not an action: the API root, OPTIONS, or a method the route
            # does not map, which the view answers with a 405.
            return True
        required = getattr(view, "required_scopes", {}).get(action)
        if not required:
            raise InsufficientScope(
                "This action grants no scope and cannot be performed.",
                details={"action": action},
            )
        # A Django user (Basic or session) holds every scope for the
        # transition; the bearer path has already produced a Principal.
        principal = principal_of(request)
        if any(principal.has(scope) for scope in required):
            return True
        raise InsufficientScope(details={"required": sorted(str(scope) for scope in required)})

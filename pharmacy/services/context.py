"""Who is writing, and from where.

Every service write takes a `WriteContext` first. It is built once per
request by the base viewset and carried down so the layers below the view
never touch the request object; tests that call a service directly build one
by hand.

`staff` is the person acting, as distinct from `user`, the login: there is
no link between the two, so the request names the person in the X-Staff-Id
header. When a JWT carries staff identity later, this is the one place that
changes.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class WriteContext:
    user: object
    staff: object = None
    path: str = ""
    method: str = ""
    request_id: str = ""

    @classmethod
    def from_request(cls, request, staff=None):
        return cls(
            user=request.user,
            staff=staff,
            path=request.path,
            method=request.method,
            request_id=getattr(request, "request_id", ""),
        )

"""Who is writing, and from where.

Every service write takes a `WriteContext` first. It is built once per
request by the base viewset (and by the admin) and carried down so the
layers below the view never touch the request object; tests that call a
service directly build one by hand.

`principal` is who the credential says is acting. A bearer token names a
`Staff` row (`staff`); a Django session or Basic credential names a login
(`user`). Exactly one of the two is set, and the change history records
whichever it is.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class WriteContext:
    principal: object
    path: str = ""
    method: str = ""
    request_id: str = ""

    @property
    def staff(self):
        return getattr(self.principal, "staff", None)

    @property
    def user(self):
        return getattr(self.principal, "django_user", None)

    @classmethod
    def from_request(cls, request, principal):
        return cls(
            principal=principal,
            path=request.path,
            method=request.method,
            request_id=getattr(request, "request_id", ""),
        )

"""Who a request is for, as the token described them."""

from dataclasses import dataclass, field

from pharmacy.models import highest_role

from .scopes import ALL_SCOPES


@dataclass(frozen=True)
class Principal:
    """The identity behind a request.

    From a Logto token: every field is a claim, `staff` is the row the
    subject maps to. From a Django session or Basic credential (the admin,
    and the transition): `external_user_id` is `django:<pk>`, every scope is
    held, and `staff` is None -- such an actor can do everything except the
    two writes that must name a person.
    """

    external_user_id: str
    email: str = ""
    name: str = ""
    given_name: str = ""
    family_name: str = ""
    roles: tuple = ()
    pharmacy_id: str | None = None
    registration_state: str = ""
    registration_number: str = ""
    supervising_external_user_id: str = ""
    scopes: frozenset = field(default_factory=frozenset)
    staff: object = None
    django_user: object = None

    # What DRF's IsAuthenticated and request.user.is_authenticated expect.
    is_authenticated = True
    is_anonymous = False

    @property
    def pk(self):
        # DRF's UserRateThrottle keys on request.user.pk: one bucket per
        # identity, the Django user's for a login.
        return self.django_user.pk if self.django_user is not None else self.external_user_id

    @property
    def role(self):
        return highest_role(self.roles)

    @property
    def is_django_actor(self):
        return self.django_user is not None

    def has(self, scope):
        return scope in self.scopes

    @classmethod
    def from_claims(cls, claims):
        return cls(
            external_user_id=claims["sub"],
            email=claims.get("email") or "",
            name=claims.get("name") or "",
            given_name=claims.get("given_name") or "",
            family_name=claims.get("family_name") or "",
            roles=tuple(claims.get("roles") or ()),
            pharmacy_id=claims.get("pharmacy_id") or None,
            registration_state=claims.get("registration_state") or "",
            registration_number=claims.get("registration_number") or "",
            supervising_external_user_id=claims.get("supervising_sub") or "",
            scopes=frozenset((claims.get("scope") or "").split()),
        )

    @classmethod
    def from_django_user(cls, user):
        return cls(
            external_user_id=f"django:{user.pk}",
            email=user.email or "",
            name=user.get_full_name() or user.get_username(),
            scopes=ALL_SCOPES,
            django_user=user,
        )

    def __str__(self):
        return self.email or self.external_user_id

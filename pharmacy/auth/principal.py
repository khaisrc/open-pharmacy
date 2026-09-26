"""Who a request is for, as the token described them."""

from dataclasses import dataclass, field

from pharmacy.models import highest_role

from . import claims as c
from .scopes import ALL_SCOPES


@dataclass(frozen=True)
class Principal:
    """The identity behind a request.

    From a bearer token: every field is a contract claim (`pharmacy.auth.claims`),
    `staff` is the row the subject maps to. From a Django session or Basic credential (the admin,
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
        """From contract claims -- what a provider adapter's `claims()`
        returns. The one place the contract's names are read."""
        return cls(
            external_user_id=claims[c.SUBJECT],
            email=claims.get(c.EMAIL) or "",
            name=claims.get(c.NAME) or "",
            given_name=claims.get(c.GIVEN_NAME) or "",
            family_name=claims.get(c.FAMILY_NAME) or "",
            roles=tuple(claims.get(c.ROLES) or ()),
            pharmacy_id=claims.get(c.PHARMACY_ID) or None,
            registration_state=claims.get(c.REGISTRATION_STATE) or "",
            registration_number=claims.get(c.REGISTRATION_NUMBER) or "",
            supervising_external_user_id=claims.get(c.SUPERVISING_USER_ID) or "",
            scopes=frozenset((claims.get(c.SCOPE) or "").split()),
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

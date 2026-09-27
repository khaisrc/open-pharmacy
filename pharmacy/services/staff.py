"""Staff: the directory, and the identity record behind a token.

`identify` is called by the authentication class on every bearer request:
it finds the row for the token's subject, or creates it on first sight,
and refuses a token whose identity no longer matches what that row
recorded, or whose row is inactive. That comparison is what makes "identity per subject is
immutable" a rule rather than a convention -- the identity provider issues
a token for an edited user just the same.
"""

import uuid

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction

from pharmacy import repositories
from pharmacy.exceptions import Conflict, Unauthenticated
from pharmacy.models import Staff, StaffRole, highest_role
from pharmacy.models.staff import credential_problems

from . import history
from .base import CrudService
from .context import WriteContext

# The columns a token fixes for the life of the row. `is_active` is not
# among them: it is local, and a supervisor (`supervising_user_id`) is the
# identity provider's own record rather than part of who the intern is.
IDENTITY_FIELDS = (
    "email",
    "first_name",
    "last_name",
    "role",
    "roles",
    "pharmacy_id",
    "registration_state",
    "registration_number",
    "certification_body",
    "certification_number",
)
# What an admin may still change on a row the identity provider owns.
LOCALLY_EDITABLE = frozenset({"is_active"})


class StaffService(CrudService):
    repository = repositories.staff
    # A location's pharmacist-in-charge must stay resolvable; revoke the
    # designation first.
    protected_relations = (("pic_of_locations", "pharmacy locations"),)

    def identify(self, principal, *, path="", method="", request_id=""):
        """The `Staff` row for a token's principal: found, or created on
        first sight. Raises `Unauthenticated` when the token cannot be
        turned into a valid, unchanged, active identity."""
        staff = self.repository.find(external_user_id=principal.external_user_id).first()
        if staff is not None:
            return self._accept(staff, principal)
        return self._register(principal, path=path, method=method, request_id=request_id)

    def _accept(self, staff, principal):
        self._refuse_if_changed(staff, principal)
        # Suspending the user at the provider stops new tokens; this stops
        # one already issued, and keeps a retired predecessor out.
        if not staff.is_active:
            raise Unauthenticated(
                "This staff member is inactive.",
                code="staff_inactive",
                details={"external_user_id": staff.external_user_id},
            )
        return staff

    def create(self, ctx, **fields):
        # A hand-typed row holds the one role it was given.
        fields.setdefault("roles", [fields["role"]])
        return super().create(ctx, **fields)

    def describe(self, principal):
        """The row a token's principal would register as, without writing:
        the validation a first request gets, for a seed to check a user
        before anyone signs in. Raises `Unauthenticated` as `identify` would."""
        fields = self._claims_as_row(principal)
        self._refuse_if_incomplete(fields)
        return fields

    def update(self, ctx, instance, **fields):
        self._refuse_external_edit(instance, fields)
        if "role" in fields and not instance.is_external:
            fields["roles"] = [fields["role"]]
        return super().update(ctx, instance, **fields)

    def delete(self, ctx, instance):
        self._refuse_external_edit(instance, {"deleted": True})
        super().delete(ctx, instance)

    def _refuse_external_edit(self, instance, fields):
        changed = {name for name, value in fields.items() if getattr(instance, name, None) != value}
        if instance.is_external and changed - LOCALLY_EDITABLE:
            raise Conflict(
                "This staff member's identity is managed by the identity provider; only "
                "is_active can be changed here.",
                code="identity_managed_externally",
                details={"external_user_id": instance.external_user_id, "fields": sorted(changed)},
            )

    def _claims_as_row(self, principal):
        """The row's fields from the claims, with the pharmacy looked up."""
        fields = self._claimed_fields(principal)
        pharmacy_id = fields.pop("pharmacy_id")
        pharmacy = None
        if pharmacy_id is not None:
            pharmacy = repositories.pharmacies.find(pk=pharmacy_id).first()
            if pharmacy is None:
                raise Unauthenticated(
                    "The token's pharmacy_id names no pharmacy here.",
                    code="identity_incomplete",
                    details={"pharmacy_id": principal.pharmacy_id},
                )
        fields["pharmacy"] = pharmacy
        return fields

    def _claimed_fields(self, principal):
        """The row's fields from the claims alone, `pharmacy_id` as a UUID.
        No query: this runs on every bearer request."""
        role = highest_role(principal.roles)
        if role is None:
            raise Unauthenticated(
                "The token names no recognised role.",
                code="identity_incomplete",
                details={"roles": list(principal.roles)},
            )
        if principal.supervising_external_user_id == "" and role == StaffRole.INTERN:
            raise Unauthenticated(
                "An intern's token must name a supervising pharmacist or technician "
                "(supervising_user_id).",
                code="supervisor_required",
            )
        return {
            "external_user_id": principal.external_user_id,
            "email": principal.email,
            "first_name": principal.given_name or principal.name or principal.email,
            "last_name": principal.family_name,
            "role": role,
            "roles": sorted(principal.roles),
            "pharmacy_id": self._pharmacy_id(principal),
            "registration_state": (principal.registration_state or "").upper(),
            "registration_number": (principal.registration_number or "").upper(),
            "certification_body": (principal.certification_body or "").upper(),
            "certification_number": (principal.certification_number or "").strip(),
        }

    @staticmethod
    def _pharmacy_id(principal):
        if principal.pharmacy_id is None:
            return None
        try:
            return uuid.UUID(str(principal.pharmacy_id).strip())
        except ValueError as exc:
            raise Unauthenticated(
                "The token's pharmacy_id is not a pharmacy id.",
                code="identity_incomplete",
                details={"pharmacy_id": principal.pharmacy_id},
            ) from exc

    def _refuse_if_changed(self, staff, principal):
        claimed = self._claimed_fields(principal)
        if staff.role == StaffRole.INTERN and not principal.supervising_external_user_id:
            raise Unauthenticated(
                "An intern's token must name a supervising pharmacist or technician "
                "(supervising_user_id).",
                code="supervisor_required",
            )
        differing = sorted(
            name for name in IDENTITY_FIELDS if getattr(staff, name) != claimed[name]
        )
        if differing:
            raise Unauthenticated(
                "This user's identity changed in the identity server; deactivate it and "
                "create a new user.",
                code="identity_changed",
                details={"external_user_id": staff.external_user_id, "fields": differing},
            )

    @staticmethod
    def _refuse_if_incomplete(fields):
        problems = credential_problems(
            fields["role"],
            fields["registration_state"],
            fields["registration_number"],
            fields["certification_body"],
            fields["certification_number"],
            pharmacy_id=fields["pharmacy"].pk if fields["pharmacy"] else None,
        )
        if problems:
            raise Unauthenticated(
                "The token does not describe a valid staff member: " + "; ".join(problems.values()),
                code="identity_incomplete",
                details=problems,
            )

    def _register(self, principal, *, path, method, request_id):
        fields = self._claims_as_row(principal)
        self._refuse_if_incomplete(fields)
        with transaction.atomic():
            # A UI's first page load sends several requests at once: the
            # lock makes the others wait, then find the row the first made.
            self.repository.lock_subject(principal.external_user_id)
            existing = self.repository.find(external_user_id=principal.external_user_id).first()
            if existing is not None:
                return self._accept(existing, principal)
            staff = Staff(**fields)
            # The predecessors must be inactive before the successor can hold
            # their registration (the unique constraint is over active rows,
            # and full_clean checks it), and the successor must exist before
            # any history row can name it as the actor -- hence the order:
            # retire, validate, insert, then record.
            retired = self._retire_predecessors(staff)
            try:
                staff.full_clean()
            except DjangoValidationError as exc:
                raise Unauthenticated(
                    "The token does not describe a valid staff member.",
                    code="identity_incomplete",
                    details=exc.message_dict,
                ) from exc
            self.repository.save(staff)
            ctx = WriteContext(
                principal=_Actor(staff), path=path, method=method, request_id=request_id
            )
            for predecessor, before in retired:
                history.record(ctx, predecessor, before)
            history.record(ctx, staff)
        return staff

    def _retire_predecessors(self, successor):
        """A new identity taking a registration, NPI or email an active row
        holds is the same person, recreated after a change: the old row stops
        being active. The email covers a technician promoted to pharmacist,
        who held no registration. Returns (row, snapshot before) pairs for
        the history."""
        lookups = []
        if successor.registration_number:
            lookups.append(
                {
                    "registration_state": successor.registration_state,
                    "registration_number": successor.registration_number,
                }
            )
        if successor.npi:
            lookups.append({"npi": successor.npi})
        if successor.email:
            lookups.append({"email": successor.email})
        holders = {}
        for lookup in lookups:
            for row in self.repository.find(is_active=True, **lookup):
                holders[row.pk] = row
        retired = []
        for predecessor in holders.values():
            before = history.snapshot(predecessor)
            predecessor.is_active = False
            self.repository.save(predecessor, update_fields=["is_active", "updated_at"])
            retired.append((predecessor, before))
        return retired


class _Actor:
    """The principal of a write made on a staff row's own behalf: the row
    being registered. Only what `WriteContext` reads."""

    def __init__(self, staff):
        self.staff = staff
        self.django_user = None

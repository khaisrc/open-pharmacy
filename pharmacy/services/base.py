"""The service layer: where the rules are.

A service owns every write to its model -- the transaction, the lock, the
refusals -- and reaches the database only through a repository. Views call a
service for anything that changes state and read directly from repositories:
a GET has queries and no rules, so a service read would be an empty
pass-through. The six reference resources subclass `CrudService` and set
nothing but `repository` and `protected_relations`; the file is thin on
purpose, a named place for the rule that arrives later and one uniform call
path now.

Every write here records its change history (`services.history`) in the
same transaction, so a refused or rolled-back write leaves no rows.
"""

from django.db import transaction

from pharmacy.exceptions import Conflict

from . import history


class CrudService:
    repository = None
    # (related_name, human label) pairs checked before a delete is allowed.
    # safedelete bypasses Django's on_delete when soft-deleting, so PROTECT
    # never fires and the guard lives here. PROTECT stays on the FKs as a
    # backstop for a genuine hard_delete() in some future purge job.
    protected_relations = ()

    def create(self, ctx, **fields):
        with transaction.atomic():
            instance = self.repository.create(**fields)
            history.record(ctx, instance)
        return instance

    def update(self, ctx, instance, **fields):
        with transaction.atomic():
            before = history.snapshot(instance)
            for name, value in fields.items():
                setattr(instance, name, value)
            self.repository.save(instance)
            history.record(ctx, instance, before)
        return instance

    def delete(self, ctx, instance):
        blocking = self.repository.live_reference_counts(instance, self.protected_relations)
        if blocking:
            raise Conflict(
                "Cannot delete a record that live records still reference.",
                code="referenced",
                details={"referenced_by": blocking},
            )
        with transaction.atomic():
            before = history.snapshot(instance)
            self.repository.delete(instance)
            history.record(ctx, instance, before)

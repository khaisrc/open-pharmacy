"""The data access layer: the only place a query is written.

A repository hands out querysets and performs the persistence calls; it holds
no rules. Views read through `all()`, services write through the rest, and
nothing outside this package names a manager (`objects`, `all_objects`) -- a
test in `tests/test_layering.py` keeps that true. Querysets, not lists, cross
the boundary on purpose: DRF's filtering, ordering, pagination and
`nested_list` all need one, so hiding the ORM entirely would mean rebuilding
them. Each per-model subclass owns the joins every read of that model needs,
which is what keeps a locked re-read in a write path identical to the list
read.
"""


class Repository:
    model = None

    def all(self):
        """Live rows, with the joins every read of this model needs."""
        return self.model.objects.all()

    def find(self, **lookup):
        """Live rows matching `lookup`; the duplicate checks and header
        lookups go through here rather than filtering a queryset elsewhere."""
        return self.all().filter(**lookup)

    def get(self, pk):
        return self.all().get(pk=pk)

    def get_locked(self, pk):
        """Re-read one row under a lock, keeping the joins the response needs.

        `of=("self",)` locks this model's row only: nullable joins are outer
        joins, which Postgres refuses to lock, and a write path never touches
        the related rows anyway. Not usable on an `all()` that aggregates --
        FOR UPDATE is incompatible with GROUP BY -- so a repository whose
        `all()` annotates must override this if a lock is ever needed.
        """
        return self.all().select_for_update(of=("self",)).get(pk=pk)

    def create(self, **fields):
        return self.model.objects.create(**fields)

    def save(self, instance, *, update_fields=None):
        instance.save(update_fields=update_fields)
        return instance

    def delete(self, instance):
        """Soft delete: the row stays, `all()` stops returning it."""
        instance.delete()

    def live_reference_counts(self, instance, relations):
        """{label: count} for each (related_name, label) in `relations` that
        still has live rows pointing at `instance`."""
        counts = {}
        for relation, label in relations:
            count = getattr(instance, relation).count()
            if count:
                counts[label] = count
        return counts

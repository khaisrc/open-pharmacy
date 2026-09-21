from pharmacy.exceptions import duplicate


def reject_live_duplicate(queryset, instance, *, record, field, label, value):
    """Raise the duplicate 409 when a live row other than `instance` matches.

    DRF generates no uniqueness validator for a UniqueConstraint that has a
    condition, so the partial index is invisible to it and the live-row check
    has to be explicit. `queryset` is already filtered to the candidate rows;
    the exception handler maps the IntegrityError from a concurrent create to
    the same 409.
    """
    if instance is not None:
        queryset = queryset.exclude(pk=instance.pk)
    existing = queryset.first()
    if existing is not None:
        raise duplicate(record, field, label, value=value, existing=existing)

from rest_framework import viewsets

from pharmacy.exceptions import Conflict


class SoftDeleteModelViewSet(viewsets.ModelViewSet):
    """Every viewset uses the live-only `objects` manager, and refuses a delete
    that would orphan live references.

    safedelete bypasses Django's on_delete when soft-deleting, so PROTECT never
    fires and the guard has to live here. PROTECT stays on the FKs as a backstop
    for a genuine hard_delete() in some future purge job; the exception handler
    maps the ProtectedError that would raise, so nothing here catches it.
    """

    # (related_name, human label) pairs checked before a delete is allowed.
    protected_relations = ()

    def perform_destroy(self, instance):
        blocking = {}
        for relation, label in self.protected_relations:
            count = getattr(instance, relation).count()
            if count:
                blocking[label] = count
        if blocking:
            raise Conflict(
                "Cannot delete a record that live records still reference.",
                code="referenced",
                details={"referenced_by": blocking},
            )
        instance.delete()


def nested_list(viewset_class, request, **scope):
    """Serve `viewset_class`'s list action restricted to `scope`.

    A nested route such as /patients/{id}/prescriptions/ is the child list with
    one filter pinned. Running it through the child viewset keeps the
    select_related list, filterset, search, ordering and pagination defined in
    one place, so ?status= works there exactly as it does at the top level.
    """
    view = viewset_class(request=request, format_kwarg=None, action="list")
    view.kwargs = {}
    queryset = view.filter_queryset(view.get_queryset().filter(**scope))
    page = view.paginate_queryset(queryset)
    serializer = view.get_serializer(page, many=True)
    return view.get_paginated_response(serializer.data)

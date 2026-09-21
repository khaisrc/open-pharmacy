from django.utils.functional import cached_property
from rest_framework import viewsets
from rest_framework.generics import get_object_or_404

from pharmacy.exceptions import Conflict
from pharmacy.models import Pharmacy


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


class PharmacyScopedViewSet(SoftDeleteModelViewSet):
    """A sub-resource of a pharmacy: /pharmacies/{pharmacy_id}/<children>/.

    The parent comes from the URL, so every action is scoped to it before any
    client-supplied filter applies, a create attaches it without a body field,
    and an unknown or deleted pharmacy is a 404 on every route rather than an
    empty list -- the same answer a bad {id} gives, so a client cannot tell
    the two apart. The model's FK to Pharmacy must be named `pharmacy`.
    """

    pharmacy_url_kwarg = "pharmacy_id"

    @cached_property
    def pharmacy(self):
        # DRF's get_object_or_404 also turns a malformed UUID into a 404.
        return get_object_or_404(Pharmacy.objects, pk=self.kwargs[self.pharmacy_url_kwarg])

    @property
    def generating_schema(self):
        # drf-spectacular instantiates the viewset with no URL kwargs.
        return getattr(self, "swagger_fake_view", False)

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.generating_schema:
            return queryset.none()
        return queryset.filter(pharmacy=self.pharmacy)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if not self.generating_schema:
            context["pharmacy"] = self.pharmacy
        return context

    def perform_create(self, serializer):
        serializer.save(pharmacy=self.pharmacy)

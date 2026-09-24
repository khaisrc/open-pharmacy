import uuid

from django.utils.functional import cached_property
from rest_framework import viewsets
from rest_framework.generics import get_object_or_404

from pharmacy import repositories
from pharmacy.exceptions import InvalidRequest
from pharmacy.services import WriteContext

STAFF_HEADER = "X-Staff-Id"


class ServiceViewSetMixin:
    """An adapter: parse the request, call the service, serialize the result.

    Reads come from `repository.all()`, the live-only queryset with the joins
    every read needs; writes go through `service`, which owns the rules, the
    transaction and the delete guard. `serializer.save()` is never called --
    a serializer here validates and renders, and the service is the only
    thing that persists.
    """

    repository = None
    service = None

    @cached_property
    def ctx(self):
        return WriteContext.from_request(self.request, staff=self._staff_from_header())

    def _staff_from_header(self):
        """The staff member the X-Staff-Id header names, or None.

        Resolved only when a write asks for the context, so a read never
        looks at the header. When present it must be a live, active staff
        member: a wrong header is a 400 before any service runs, rather than
        a fill silently attributed to nobody.
        """
        value = self.request.headers.get(STAFF_HEADER)
        if not value:
            return None
        try:
            pk = uuid.UUID(value)
        except ValueError:
            pk = None
        staff = repositories.staff.find(pk=pk, is_active=True).first() if pk else None
        if staff is None:
            raise InvalidRequest(
                f"{STAFF_HEADER} does not name a live, active staff member.",
                code="invalid_staff",
                details={"header": STAFF_HEADER, "value": value},
            )
        return staff

    def get_queryset(self):
        return self.repository.all()

    def perform_create(self, serializer):
        serializer.instance = self.service.create(self.ctx, **serializer.validated_data)

    def perform_update(self, serializer):
        serializer.instance = self.service.update(
            self.ctx, serializer.instance, **serializer.validated_data
        )

    def perform_destroy(self, instance):
        self.service.delete(self.ctx, instance)


class SoftDeleteModelViewSet(ServiceViewSetMixin, viewsets.ModelViewSet):
    """Full CRUD over a soft-deleted model, through the layers above."""


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


class ScopedViewSetMixin:
    """A sub-resource: /<parents>/{parent_id}/<children>/.

    The parent comes from the URL, so every action is scoped to it before any
    client-supplied filter applies, a create attaches it without a body field,
    and an unknown or deleted parent is a 404 on every route rather than an
    empty list -- the same answer a bad {id} gives, so a client cannot tell
    the two apart. Subclasses name the parent's repository, the URL kwarg and
    the child model's FK field to it.
    """

    parent_repository = None
    parent_url_kwarg = None
    parent_field = None

    @cached_property
    def parent(self):
        # DRF's get_object_or_404 also turns a malformed UUID into a 404.
        return get_object_or_404(
            self.parent_repository.all(), pk=self.kwargs[self.parent_url_kwarg]
        )

    @property
    def generating_schema(self):
        # drf-spectacular instantiates the viewset with no URL kwargs.
        return getattr(self, "swagger_fake_view", False)

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.generating_schema:
            return queryset.none()
        return queryset.filter(**{self.parent_field: self.parent})

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if not self.generating_schema:
            context[self.parent_field] = self.parent
        return context

    def perform_create(self, serializer):
        serializer.instance = self.service.create(
            self.ctx, **{self.parent_field: self.parent}, **serializer.validated_data
        )


class PharmacyScopedViewSet(ScopedViewSetMixin, SoftDeleteModelViewSet):
    """Full CRUD on a child of a pharmacy: /pharmacies/{pharmacy_id}/<children>/.
    The model's FK to Pharmacy must be named `pharmacy`."""

    parent_repository = repositories.pharmacies
    parent_url_kwarg = "pharmacy_id"
    parent_field = "pharmacy"

    @property
    def pharmacy(self):
        return self.parent

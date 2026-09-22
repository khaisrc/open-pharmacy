from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AddressViewSet,
    FillViewSet,
    MedicationViewSet,
    PatientViewSet,
    PharmacyLocationViewSet,
    PharmacyViewSet,
    PrescriberViewSet,
    PrescriptionFillsViewSet,
    PrescriptionViewSet,
    StaffViewSet,
    health,
)

# No version prefix here: config/urls.py mounts this under /api/v1/.
router = DefaultRouter()
router.register("prescriptions", PrescriptionViewSet, basename="prescription")
router.register("patients", PatientViewSet, basename="patient")
router.register("prescribers", PrescriberViewSet, basename="prescriber")
router.register("medications", MedicationViewSet, basename="medication")
router.register("pharmacies", PharmacyViewSet, basename="pharmacy")
# Detail only: the viewset has no list or create, so the router lays out
# /fills/{id}/ and its actions and nothing at /fills/.
router.register("fills", FillViewSet, basename="fill")
router.register("addresses", AddressViewSet, basename="address")


LIST_METHODS = {"get": "list", "post": "create"}
DETAIL_METHODS = {
    "get": "retrieve",
    "put": "update",
    "patch": "partial_update",
    "delete": "destroy",
}


def sub_resource(prefix, viewset, basename, *, list_methods=None, detail_methods=None):
    """Routes for a scoped viewset under `prefix`, the way the router would
    lay them out one level down: list and detail, plus every detail @action
    the viewset declares. A viewset without, say, PUT or DELETE names the
    methods it does serve; `detail_methods={}` is a collection with no
    detail route at all, for a child that has a top-level id of its own.

    DRF's router does not nest, and a third-party nested router would only
    produce these same patterns. `<str:...>` rather than `<uuid:...>` so a
    malformed id reaches the viewset and comes back as the API's own JSON
    404, exactly as it does on the router's routes.
    """
    if detail_methods is None:
        detail_methods = DETAIL_METHODS
    list_view = viewset.as_view(list_methods or LIST_METHODS, basename=basename, detail=False)
    patterns = [path(prefix, list_view, name=f"{basename}-list")]
    if not detail_methods:
        return patterns
    detail_view = viewset.as_view(detail_methods, basename=basename, detail=True)
    patterns.append(path(f"{prefix}<str:pk>/", detail_view, name=f"{basename}-detail"))
    for extra in viewset.get_extra_actions():
        if not extra.detail:
            continue
        # dict(): MethodMapper overrides .get() as the decorator for GET.
        view = viewset.as_view(dict(extra.mapping), basename=basename, detail=True, **extra.kwargs)
        patterns.append(
            path(f"{prefix}<str:pk>/{extra.url_path}/", view, name=f"{basename}-{extra.url_name}")
        )
    return patterns


urlpatterns = [
    path("health/", health, name="health"),
    path("", include(router.urls)),
    *sub_resource(
        "pharmacies/<str:pharmacy_id>/locations/", PharmacyLocationViewSet, "pharmacy-location"
    ),
    *sub_resource("pharmacies/<str:pharmacy_id>/staff/", StaffViewSet, "pharmacy-staff"),
    *sub_resource(
        "prescriptions/<str:prescription_id>/fills/",
        PrescriptionFillsViewSet,
        "prescription-fill",
        detail_methods={},
    ),
]

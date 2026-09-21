from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AddressViewSet,
    MedicationViewSet,
    PatientViewSet,
    PharmacyLocationViewSet,
    PharmacyViewSet,
    PrescriberViewSet,
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
router.register("addresses", AddressViewSet, basename="address")


def sub_resource(prefix, viewset, basename):
    """Routes for a PharmacyScopedViewSet under `prefix`, the way the router
    would lay them out one level down: list and detail, plus every detail
    @action the viewset declares.

    DRF's router does not nest, and a third-party nested router would only
    produce these same patterns. `<str:...>` rather than `<uuid:...>` so a
    malformed id reaches the viewset and comes back as the API's own JSON
    404, exactly as it does on the router's routes.
    """
    list_view = viewset.as_view({"get": "list", "post": "create"}, basename=basename, detail=False)
    detail_view = viewset.as_view(
        {"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"},
        basename=basename,
        detail=True,
    )
    patterns = [
        path(prefix, list_view, name=f"{basename}-list"),
        path(f"{prefix}<str:pk>/", detail_view, name=f"{basename}-detail"),
    ]
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
]

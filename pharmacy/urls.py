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
    health,
)

# No version prefix here: config/urls.py mounts this under /api/v1/.
router = DefaultRouter()
router.register("prescriptions", PrescriptionViewSet, basename="prescription")
router.register("patients", PatientViewSet, basename="patient")
router.register("prescribers", PrescriberViewSet, basename="prescriber")
router.register("medications", MedicationViewSet, basename="medication")
router.register("pharmacies", PharmacyViewSet, basename="pharmacy")
router.register("pharmacy-locations", PharmacyLocationViewSet, basename="pharmacylocation")
router.register("addresses", AddressViewSet, basename="address")

urlpatterns = [
    path("health/", health, name="health"),
    path("", include(router.urls)),
]

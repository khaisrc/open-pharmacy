from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

# Versioning happens once, here, so app-level routes stay version-agnostic.
urlpatterns = [
    # Session-authenticated, for the superuser seed_local creates. Every
    # write it makes goes through the same services the API uses.
    path("admin/", admin.site.urls),
    path("api/v1/", include(("pharmacy.urls", "pharmacy"), namespace="v1")),
    path("api/v1/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/v1/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path("api/v1/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
]

"""Tells drf-spectacular how the bearer scheme looks, so the docs offer an
Authorize box that takes an access token."""

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class BearerScheme(OpenApiAuthenticationExtension):
    target_class = "pharmacy.auth.authentication.BearerTokenAuthentication"
    name = "bearer"

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "An access token from the identity provider for this API's audience.",
        }

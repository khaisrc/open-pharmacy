"""Tells drf-spectacular how the bearer scheme looks, so the docs offer an
Authorize box that takes a Logto access token."""

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class LogtoJWTScheme(OpenApiAuthenticationExtension):
    target_class = "pharmacy.auth.authentication.LogtoJWTAuthentication"
    name = "logtoBearer"

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "A Logto access token for this API's resource indicator.",
        }

"""Settings shared by every tier.

This module never reads .env.local. If it did, a value the deployment forgot to
supply would silently fall back to a committed local one, and require_settings()
below would see nothing missing.
"""

import os
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()

# A secrets sidecar may deliver values either as a file on a shared volume or as
# process environment variables. Reading the file first, with overwrite=False,
# supports both without a later code change.
_secrets_file = os.environ.get("SECRETS_FILE")
if _secrets_file and Path(_secrets_file).exists():
    environ.Env.read_env(_secrets_file, overwrite=False)


def require_settings(names):
    """Report every missing variable at once, not one restart at a time."""
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        raise ImproperlyConfigured(
            "Missing required environment variables: " + ", ".join(sorted(missing))
        )


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "whitenoise.runserver_nostatic",
    "rest_framework",
    "django_filters",
    "corsheaders",
    "drf_spectacular",
    "drf_spectacular_sidecar",
    "pharmacy",
]

MIDDLEWARE = [
    "pharmacy.middleware.RequestIDMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = Path(os.environ.get("STATIC_ROOT", BASE_DIR / "staticfiles"))
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    # A Logto bearer token first; Basic and session stay for the transition
    # (and for the admin), with every scope.
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "pharmacy.auth.LogtoJWTAuthentication",
        "rest_framework.authentication.BasicAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
        "pharmacy.auth.HasScope",
    ],
    "DEFAULT_PAGINATION_CLASS": "pharmacy.pagination.DefaultPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_VERSIONING_CLASS": "rest_framework.versioning.URLPathVersioning",
    "DEFAULT_VERSION": "v1",
    "ALLOWED_VERSIONS": ["v1"],
    "EXCEPTION_HANDLER": "pharmacy.exceptions.exception_handler",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": env.str("THROTTLE_ANON", default="100/min"),
        "user": env.str("THROTTLE_USER", default="1000/min"),
    },
}

# Swagger groups operations by tag. The order here is the order the UI renders;
# left alphabetical, the docs would open on Addresses instead of Prescriptions.
SPECTACULAR_SETTINGS = {
    "TITLE": "open-pharmacy API",
    "DESCRIPTION": "Reference implementation of a backend system for a US pharmacy",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SWAGGER_UI_DIST": "SIDECAR",
    "SWAGGER_UI_FAVICON_HREF": "SIDECAR",
    "REDOC_DIST": "SIDECAR",
    "SCHEMA_PATH_PREFIX": "/api/v1",
    "SWAGGER_UI_SETTINGS": {
        "persistAuthorization": True,
        "docExpansion": "list",
        "operationsSorter": "method",
        "displayRequestDuration": True,
    },
    "TAGS": [
        {"name": "Prescriptions", "description": "Prescriptions and their lifecycle."},
        {
            "name": "Fills",
            "description": "Dispensings of a prescription, from pending to completed.",
        },
        {"name": "Patients", "description": "People a prescription is written for."},
        {"name": "Prescribers", "description": "Clinicians who write prescriptions."},
        {"name": "Medications", "description": "Products a prescription names, keyed by NDC."},
        {
            "name": "Pharmacies",
            "description": "Pharmacy organizations, from independents to chains.",
        },
        {
            "name": "Pharmacy Locations",
            "description": "Individual dispensing sites, keyed by NCPDP ID.",
        },
        {
            "name": "Staff",
            "description": "Pharmacists, technicians and interns employed by a pharmacy.",
        },
        {"name": "Addresses", "description": "Postal addresses shared by the records above."},
        {"name": "System", "description": "Unauthenticated service endpoints."},
    ],
}

# The pharmacy this deployment is the backend for, identified by its location's
# NCPDP ID. A prescription written here is dispensed at that location, so the
# API fills it in when a client does not name one. It is a setting rather than a
# constant because the same code serves a different store in each environment.
HOME_PHARMACY_LOCATION_NCPDP_ID = env.str("HOME_PHARMACY_LOCATION_NCPDP_ID", default="4417829")

# The identity server. Access tokens must carry issuer {LOGTO_ENDPOINT}/oidc
# -- the public URL, which is what Logto writes into them -- and audience
# LOGTO_API_RESOURCE, the API resource indicator configured in Logto. The
# keys are fetched from LOGTO_INTERNAL_ENDPOINT when that is set: the
# address this service reaches Logto at when it is not the public one (the
# Compose network, a private subnet behind a proxy). uat and prod require
# the first and the last; local points at the Compose service.
LOGTO_ENDPOINT = env.str("LOGTO_ENDPOINT", default="http://localhost:8441")
LOGTO_INTERNAL_ENDPOINT = env.str("LOGTO_INTERNAL_ENDPOINT", default="")
LOGTO_API_RESOURCE = env.str("LOGTO_API_RESOURCE", default="https://api.open-pharmacy.local")
# Local only: a PEM private key the `mint_token` command signs dev tokens
# with, and the verifier accepts alongside Logto's keys. Never set outside
# local; `local.py` is the only settings module that reads it.
LOGTO_DEV_SIGNING_KEY = ""


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "pharmacy.logging.JSONFormatter"}},
    "handlers": {
        "stdout": {"class": "logging.StreamHandler", "formatter": "json"},
    },
    "root": {"handlers": ["stdout"], "level": "INFO"},
    "loggers": {
        "django.request": {"handlers": ["stdout"], "level": "INFO", "propagate": False},
    },
}

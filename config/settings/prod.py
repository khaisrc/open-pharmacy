"""Production. Strictest tier, plus guards against the classic incidents."""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import env, require_settings

require_settings(["SECRET_KEY", "DATABASE_URL", "ALLOWED_HOSTS", "AUTH_ISSUER", "AUTH_AUDIENCE"])

LOCAL_SECRET_KEY = "local-only-insecure-do-not-use-anywhere-else"

SECRET_KEY = env.str("SECRET_KEY")
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")
DATABASES = {"default": env.db("DATABASE_URL")}
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])

if SECRET_KEY == LOCAL_SECRET_KEY:
    raise ImproperlyConfigured("SECRET_KEY is the committed local value; refusing to start.")
if DEBUG:
    raise ImproperlyConfigured("DEBUG must be False in production; refusing to start.")
if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured(
        "ALLOWED_HOSTS must be a non-empty, non-wildcard list; refusing to start."
    )

# Basic Auth sends reversible base64 on every request, so plain HTTP is never OK.
SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_CONTENT_TYPE_NOSNIFF = True

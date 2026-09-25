"""Local development. The only module that reads the committed .env.local."""

import environ

from .base import *  # noqa: F403
from .base import BASE_DIR, env

# overwrite=False so anything already in the process environment wins over the
# committed file -- otherwise DATABASE_URL here would clobber an override.
environ.Env.read_env(BASE_DIR / ".env.local", overwrite=False)

SECRET_KEY = env.str("SECRET_KEY", default="local-only-insecure-do-not-use-anywhere-else")
DEBUG = env.bool("DEBUG", default=True)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1", "0.0.0.0"])

DATABASES = {
    "default": env.db("DATABASE_URL", default="postgres://pharmacy:pharmacy@db:5432/open_pharmacy")
}

CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=["http://localhost:3000"])

# Dev tokens from `manage.py mint_token`, for curl without a UI. Local only:
# no other settings module reads this, so the variable means nothing elsewhere.
LOGTO_DEV_SIGNING_KEY = env.str("LOGTO_DEV_SIGNING_KEY", default="")

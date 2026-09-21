import importlib
import os
from contextlib import contextmanager

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError

from pharmacy.models import Pharmacy, PharmacyLocation, Prescription


@contextmanager
def environment(**values):
    """Swap os.environ for the duration of a settings-module import."""
    original = dict(os.environ)
    os.environ.update({k: v for k, v in values.items() if v is not None})
    for key, value in values.items():
        if value is None:
            os.environ.pop(key, None)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(original)


VALID = {
    "SECRET_KEY": "a-real-production-secret",
    "DATABASE_URL": "postgres://u:p@host:5432/db",
    "ALLOWED_HOSTS": "api.example.com",
    "DEBUG": "false",
}


class TestDeployedSettingsFailLoudly:
    @pytest.mark.parametrize("module", ["config.settings.uat", "config.settings.prod"])
    @pytest.mark.parametrize("missing", ["SECRET_KEY", "DATABASE_URL", "ALLOWED_HOSTS"])
    def test_missing_secret_raises(self, module, missing):
        values = dict(VALID)
        values[missing] = None
        with environment(**values), pytest.raises(ImproperlyConfigured) as exc:
            importlib.reload(importlib.import_module(module))
        assert missing in str(exc.value)

    def test_every_missing_name_is_reported_at_once(self):
        with (
            environment(SECRET_KEY=None, DATABASE_URL=None, ALLOWED_HOSTS=None),
            pytest.raises(ImproperlyConfigured) as exc,
        ):
            importlib.reload(importlib.import_module("config.settings.prod"))
        message = str(exc.value)
        assert all(name in message for name in ("SECRET_KEY", "DATABASE_URL", "ALLOWED_HOSTS"))


class TestProdGuards:
    def test_rejects_the_committed_local_secret_key(self):
        values = dict(VALID, SECRET_KEY="local-only-insecure-do-not-use-anywhere-else")
        with environment(**values), pytest.raises(ImproperlyConfigured, match="local value"):
            importlib.reload(importlib.import_module("config.settings.prod"))

    def test_rejects_debug_true(self):
        with (
            environment(**dict(VALID, DEBUG="true")),
            pytest.raises(ImproperlyConfigured, match="DEBUG"),
        ):
            importlib.reload(importlib.import_module("config.settings.prod"))

    def test_rejects_wildcard_allowed_hosts(self):
        with (
            environment(**dict(VALID, ALLOWED_HOSTS="*")),
            pytest.raises(ImproperlyConfigured, match="ALLOWED_HOSTS"),
        ):
            importlib.reload(importlib.import_module("config.settings.prod"))

    def test_accepts_a_valid_configuration(self):
        with environment(**VALID):
            module = importlib.reload(importlib.import_module("config.settings.prod"))
        assert module.DEBUG is False
        assert module.SECURE_SSL_REDIRECT is True


@pytest.mark.django_db
class TestSeedLocal:
    def test_is_idempotent(self):
        with environment(DJANGO_ENV="local", SEED_DEMO_DATA="true"):
            call_command("seed_local")
            first = (
                Pharmacy.objects.count(),
                PharmacyLocation.objects.count(),
                Prescription.objects.count(),
            )
            call_command("seed_local")
            second = (
                Pharmacy.objects.count(),
                PharmacyLocation.objects.count(),
                Prescription.objects.count(),
            )
        assert first == second

    def test_seeds_both_pharmacy_shapes(self):
        with environment(DJANGO_ENV="local", SEED_DEMO_DATA="true"):
            call_command("seed_local")
        counts = sorted(p.locations.count() for p in Pharmacy.objects.all())
        assert counts[0] == 1, "expected an independent with a single location"
        assert counts[-1] > 1, "expected a chain with several locations"

    @pytest.mark.parametrize("env", ["uat", "prod", "staging"])
    def test_refuses_outside_local(self, env):
        with environment(DJANGO_ENV=env), pytest.raises(CommandError, match="refuses"):
            call_command("seed_local")

    def test_skips_demo_data_when_not_requested(self):
        with environment(DJANGO_ENV="local", SEED_DEMO_DATA="false"):
            call_command("seed_local")
        # Acme Pharmacy is reference data seeded by a migration, so it is the
        # baseline rather than demo output; anything beyond it would be a record
        # the command should not have created.
        assert list(Pharmacy.objects.values_list("name", flat=True)) == ["Acme Pharmacy"]

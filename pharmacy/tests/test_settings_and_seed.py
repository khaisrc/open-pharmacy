import importlib
import os
from contextlib import contextmanager

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from pharmacy.models import Pharmacy, PharmacyLocation, Prescriber, Prescription, RxCounter, Staff
from pharmacy.validators import validate_npi


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
                Staff.objects.count(),
                Prescription.objects.count(),
            )
            call_command("seed_local")
            second = (
                Pharmacy.objects.count(),
                PharmacyLocation.objects.count(),
                Staff.objects.count(),
                Prescription.objects.count(),
            )
        assert first == second

    def test_is_idempotent_across_days(self, monkeypatch):
        """The demo prescriptions are keyed on who, what and by whom, never on
        when: a boot a day later must find them rather than insert four more."""
        with environment(DJANGO_ENV="local", SEED_DEMO_DATA="true"):
            call_command("seed_local")
            first = Prescription.objects.count()
            later = timezone.now() + timezone.timedelta(days=2)
            monkeypatch.setattr(
                "pharmacy.management.commands.seed_local.timezone.now", lambda: later
            )
            call_command("seed_local")
        assert Prescription.objects.count() == first

    def test_seeded_identifiers_pass_the_api_validators(self):
        """ORM writes skip validators, so a seeded NPI the API would refuse
        could not be round-tripped through PUT."""
        with environment(DJANGO_ENV="local", SEED_DEMO_DATA="true"):
            call_command("seed_local")
        for npi in Prescriber.objects.values_list("npi", flat=True):
            validate_npi(npi)
        for npi in PharmacyLocation.objects.exclude(npi="").values_list("npi", flat=True):
            validate_npi(npi)
        for npi in Staff.objects.exclude(npi="").values_list("npi", flat=True):
            validate_npi(npi)
        for staff in Staff.objects.all():
            staff.full_clean()

    def test_seeds_a_pharmacist_in_charge(self):
        with environment(DJANGO_ENV="local", SEED_DEMO_DATA="true"):
            call_command("seed_local")
        designated = PharmacyLocation.objects.exclude(pharmacist_in_charge=None)
        assert designated.exists()
        for location in designated:
            location.full_clean()

    def test_onboards_every_pharmacy_with_an_rx_counter(self, api):
        """Every seeded pharmacy, the home one included, can number a
        prescription straight after a boot; a re-run never rewinds a counter."""
        with environment(DJANGO_ENV="local", SEED_DEMO_DATA="true"):
            call_command("seed_local")
            assert RxCounter.objects.count() == Pharmacy.objects.count()
            assert set(RxCounter.objects.values_list("last_issued", flat=True)) == {1_000_000_000}
            counter = RxCounter.objects.first()
            counter.last_issued += 5
            counter.save()
            call_command("seed_local")
            counter.refresh_from_db()
        assert counter.last_issued == 1_000_000_005
        # And the seeded demo numbers sit below the counter's range.
        assert Prescription.objects.order_by("-rx_number").first().rx_number < 1_000_000_000

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

"""Fixtures shared by every test module, and the optional embedded database.

The suite needs PostgreSQL. Under Compose that is the real server named by
DATABASE_URL. On a host with no Postgres, PHARMACY_TEST_DB=embedded makes the
session start one from the pgserver package instead -- PostgreSQL 16, not the
18 Compose runs, so Compose stays the run that counts. The variable is read
here and nowhere else; settings never see it.
"""

import logging
import os

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection, connections
from rest_framework.test import APIClient

from config.settings.base import env
from pharmacy.models import PharmacyLocation

from .factories import (
    AddressFactory,
    FillFactory,
    MedicationFactory,
    PatientFactory,
    PharmacyFactory,
    PharmacyLocationFactory,
    PrescriberFactory,
    PrescriptionFactory,
    RxCounterFactory,
    StaffFactory,
)

EMBEDDED_DB_VARIABLE = "PHARMACY_TEST_DB"

# Filled in by the session fixture below and printed in the terminal summary,
# so a run always says which Postgres it was against.
_database_report = {}


@pytest.fixture(scope="session")
def django_db_modify_db_settings(
    django_db_modify_db_settings_parallel_suffix, django_db_blocker, tmp_path_factory
):
    """pytest-django's hook for changing DATABASES before the test database
    is created. Settings are already loaded by the time any conftest is
    imported, so pointing Django at the embedded server means rewriting the
    loaded DATABASES as well as the variable; both go through the same
    env.db() parser local.py uses."""
    server = None
    if os.environ.get(EMBEDDED_DB_VARIABLE) == "embedded":
        import pgserver

        # pgserver narrates initdb and pg_ctl at INFO through the root logger,
        # which the project formats as JSON lines; that is noise in a test run.
        logging.getLogger("pgserver").setLevel(logging.WARNING)
        server = pgserver.get_server(tmp_path_factory.mktemp("pg") / "data", cleanup_mode="delete")
        os.environ["DATABASE_URL"] = server.get_uri()
        # In place: django.db.connections has already cached this very dict
        # and handed it to the default connection as its settings_dict, so
        # rebinding the key would leave both pointing at the Compose host.
        default = settings.DATABASES["default"]
        default.clear()
        default.update(env.db("DATABASE_URL"))
        connections.configure_settings(settings.DATABASES)
        source = f"embedded via pgserver, {EMBEDDED_DB_VARIABLE}=embedded"
    else:
        host = settings.DATABASES["default"]["HOST"] or "local socket"
        source = f"configured DATABASE_URL, host {host}"

    try:
        with django_db_blocker.unblock(), connection.cursor() as cursor:
            cursor.execute("SELECT version()")
            version = cursor.fetchone()[0].split(" on ")[0]
    except Exception as exc:
        # Any driver error: the point is to say which database was tried and,
        # on a bare host, what to do about it.
        hint = (
            ""
            if server
            else f" On a host without Postgres, run with {EMBEDDED_DB_VARIABLE}=embedded."
        )
        raise RuntimeError(f"Cannot reach the test database ({source}): {exc}.{hint}") from exc
    connection.close()
    _database_report["line"] = f"database: {version} ({source})"

    yield

    if server is not None:
        server.cleanup()


def pytest_terminal_summary(terminalreporter):
    if "line" in _database_report:
        terminalreporter.write_line(_database_report["line"])


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="secret")


@pytest.fixture
def api(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def anon():
    return APIClient()


@pytest.fixture
def address(db):
    return AddressFactory()


@pytest.fixture
def patient(db):
    return PatientFactory()


@pytest.fixture
def prescriber(db):
    return PrescriberFactory()


@pytest.fixture
def medication(db):
    return MedicationFactory()


@pytest.fixture
def pharmacy(db):
    return PharmacyFactory()


@pytest.fixture
def location(db):
    return PharmacyLocationFactory()


@pytest.fixture
def prescription(db):
    return PrescriptionFactory()


@pytest.fixture
def pharmacist(db, pharmacy):
    return StaffFactory(pharmacy=pharmacy)


@pytest.fixture
def technician(db, pharmacy):
    return StaffFactory(
        pharmacy=pharmacy, role="technician", registration_state="", registration_number=""
    )


@pytest.fixture
def fill(db):
    return FillFactory()


@pytest.fixture
def home_rx_counter(db):
    """A seeded counter for the home pharmacy (migration 0003's Acme row), so
    a prescription created without a location can be numbered."""
    location = PharmacyLocation.objects.get(ncpdp_id=settings.HOME_PHARMACY_LOCATION_NCPDP_ID)
    return RxCounterFactory(pharmacy=location.pharmacy, last_issued=0)


def with_staff(client, staff):
    """The client, sending `staff` in X-Staff-Id on every request."""
    client.credentials(HTTP_X_STAFF_ID=str(staff.pk))
    return client


@pytest.fixture
def as_pharmacist(api, pharmacist):
    return with_staff(api, pharmacist)


@pytest.fixture
def as_technician(api, technician):
    return with_staff(api, technician)

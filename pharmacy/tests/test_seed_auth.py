"""seed_auth against a fake of the identity provider's HTTP surface
(tests/fake_logto.py, served by the `fake_logto` fixture). A second run
against the same state must create nothing.
"""

import io
import uuid
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from pharmacy.auth import claims as c
from pharmacy.auth.scopes import ROLE_SCOPES, Scope
from pharmacy.management.commands.seed_auth import (
    CUSTOMIZER,
    TEST_PASSWORD,
    TEST_USERS,
    UI_APPLICATION,
    email_for,
)

from .test_settings_and_seed import environment

# The seed reads the home pharmacy from the database for the users' employer.
pytestmark = pytest.mark.django_db


def seed(state, settings, **env):
    settings.AUTH_ISSUER = "http://localhost:8441/oidc"
    settings.AUTH_INTERNAL_URL = state.url
    settings.AUTH_AUDIENCE = "https://api.open-pharmacy.local"
    out = io.StringIO()
    with environment(
        DJANGO_ENV="local",
        AUTH_ADMIN_USERNAME="admin",
        AUTH_ADMIN_PASSWORD="Passw0rd!",
        AUTH_INTERNAL_CONSOLE_URL=state.url,
        ADMIN_ENDPOINT="http://localhost:8442",
        **env,
    ):
        call_command("seed_auth", stdout=out)
    return out.getvalue()


def test_a_fresh_instance_gets_everything(fake_logto, settings):
    out = seed(fake_logto, settings)
    assert "Created admin user 'admin'" in out
    assert fake_logto.users == {"admin": "Passw0rd!"}
    (resource,) = fake_logto.resources.values()
    assert resource["indicator"] == "https://api.open-pharmacy.local"
    assert resource["accessTokenTtl"] == 900
    assert {s["name"] for s in fake_logto.scopes.values()} == {str(s) for s in Scope}
    names = {r["name"]: r["id"] for r in fake_logto.roles.values()}
    assert set(names) == set(ROLE_SCOPES)
    for role, wanted in ROLE_SCOPES.items():
        held = {fake_logto.scopes[s]["name"] for s in fake_logto.role_scopes[names[role]]}
        assert held == {str(s) for s in wanted}, role
    assert fake_logto.customizer["script"] == CUSTOMIZER
    assert fake_logto.consented, "the admin consented to the Management API resource"
    (app,) = fake_logto.applications.values()
    assert app["name"] == UI_APPLICATION and app["customClientMetadata"]["allowTokenExchange"]
    assert "token ok" in out and out.count("token ok") == len(TEST_USERS)


def test_the_test_users_are_created_as_listed(fake_logto, settings):
    seed(fake_logto, settings)
    by_username = {u["username"]: u for u in fake_logto.tenant_users.values()}
    assert set(by_username) == {u[0] for u in TEST_USERS}
    role_names = {r["id"]: r["name"] for r in fake_logto.roles.values()}
    for username, given, family, roles, registration, certification, supervisor in TEST_USERS:
        user = by_username[username]
        assert user["primaryEmail"] == email_for(username)
        assert user["password"] == TEST_PASSWORD
        assert user["name"] == f"{given} {family}"
        assert [role_names[r] for r in fake_logto.user_roles[user["id"]]] == roles
        custom = user["customData"]
        if "admin" in roles:
            assert c.PHARMACY_ID not in custom
        else:
            uuid.UUID(custom[c.PHARMACY_ID])  # the home pharmacy
        if registration:
            assert (custom[c.REGISTRATION_STATE], custom[c.REGISTRATION_NUMBER]) == registration
        if certification:
            assert (custom[c.CERTIFICATION_BODY], custom[c.CERTIFICATION_NUMBER]) == certification
        if supervisor:
            assert custom[c.SUPERVISING_USER_ID] == by_username[supervisor]["id"]


def test_the_roster_is_what_was_asked_for():
    counts = {}
    for _, _, _, roles, *_ in TEST_USERS:
        for role in roles:
            counts[role] = counts.get(role, 0) + 1
    assert counts == {
        "pharmacist": 2,
        "certified_technician": 5,
        "technician": 5,
        "intern": 2,
        "admin": 2,
    }


def test_a_user_whose_token_the_api_would_refuse_fails_the_seed(fake_logto, settings, monkeypatch):
    """The check is real: list a certified technician without a certification
    and the seed refuses, naming the user and the reason."""
    uncertified = ("soojin_kim", "Soo-jin", "Kim", ["certified_technician"], None, None, None)
    monkeypatch.setattr("pharmacy.management.commands.seed_auth.TEST_USERS", [uncertified])
    with pytest.raises(CommandError, match="soojin_kim.*certification"):
        seed(fake_logto, settings)


def test_the_documented_list_matches():
    """docs/identity-providers/test-users.md is the human copy of TEST_USERS."""
    doc = Path(__file__).resolve().parents[2] / "docs" / "identity-providers" / "test-users.md"
    rows = [line for line in doc.read_text().splitlines() if line.startswith("| `")]
    assert len(rows) == len(TEST_USERS)
    for row, (username, given, family, roles, registration, certification, supervisor) in zip(
        rows, TEST_USERS, strict=True
    ):
        assert f"`{username}`" in row
        assert email_for(username) in row
        assert f"{given} {family}" in row
        assert ", ".join(roles) in row
        if registration:
            assert registration[1] in row
        if certification:
            assert certification[1] in row
        if supervisor:
            assert supervisor in row


def test_a_second_run_creates_nothing(fake_logto, settings):
    seed(fake_logto, settings)
    writes = list(fake_logto.writes)
    out = seed(fake_logto, settings)
    assert "already exists" in out
    assert fake_logto.writes == writes + [("customizer",)]  # the script is simply re-put


def test_a_role_missing_a_scope_is_topped_up(fake_logto, settings):
    seed(fake_logto, settings)
    names = {r["name"]: r["id"] for r in fake_logto.roles.values()}
    verify = next(s for s in fake_logto.scopes.values() if s["name"] == "fills:verify")["id"]
    fake_logto.role_scopes[names["pharmacist"]].remove(verify)
    seed(fake_logto, settings)
    assert verify in fake_logto.role_scopes[names["pharmacist"]]
    assert ("role-scopes", "pharmacist") in fake_logto.writes


def test_customizer_emits_every_contract_claim():
    for claim in c.ALL:
        if claim in (c.SUBJECT, c.SCOPE):
            continue  # the provider's own; not the customizer's to add
        assert f"{claim}:" in CUSTOMIZER, claim


def test_refuses_outside_local():
    with environment(DJANGO_ENV="prod"), pytest.raises(CommandError):
        call_command("seed_auth")


def test_refuses_without_a_password():
    with environment(DJANGO_ENV="local", AUTH_ADMIN_PASSWORD=""), pytest.raises(CommandError):
        call_command("seed_auth")

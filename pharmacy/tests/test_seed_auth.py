"""seed_auth against a fake of the identity provider's HTTP surface.

The fake answers the dozen endpoints the command uses the way Logto does
(verified against a running instance; the shapes are recorded here), keeps
state across calls, and lets the test see what was created. A second run
against the same state must create nothing.
"""

import io
import json
import re
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import jwt
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
from .tokens import TokenIssuer

# The seed reads the home pharmacy from the database for the users' employer.
pytestmark = pytest.mark.django_db


class FakeLogto:
    """State: admin users, resources with scopes, roles with scopes, the
    customizer; and a log of every write."""

    def __init__(self):
        self.users = {}  # admin tenant: username -> password
        self.resources = {}
        self.scopes = {}
        self.roles = {}
        self.role_scopes = {}
        self.customizer = None
        self.applications = {}
        self.tenant_users = {}  # default tenant: id -> user record
        self.user_roles = {}
        self.writes = []
        self.consented = False
        self.issuer = TokenIssuer(kid="fake-logto")
        self._ids = iter(range(1, 10_000))

    def new_id(self):
        return f"id{next(self._ids)}"

    def token_for(self, user, audience, scope):
        """What the customizer would make of this user: the contract from
        the user's fields and customData, plus the roles held."""
        roles = [self.roles[r]["name"] for r in self.user_roles.get(user["id"], [])]
        granted = set()
        for role in roles:
            granted |= ROLE_SCOPES.get(role, frozenset())
        asked = set(scope.split())
        claims = {
            "sub": user["id"],
            "iat": int(time.time()),
            "exp": int(time.time()) + 900,
            "iss": "http://localhost:8441/oidc",
            "aud": audience,
            "scope": " ".join(sorted(str(s) for s in granted & asked)),
            c.EMAIL: user["primaryEmail"],
            c.NAME: user["name"],
            c.GIVEN_NAME: user["profile"]["givenName"],
            c.FAMILY_NAME: user["profile"]["familyName"],
            c.ROLES: roles,
            **user["customData"],
        }
        return jwt.encode(
            claims, self.issuer.private_key, algorithm="ES384", headers={"kid": "fake-logto"}
        )


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def body(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            kind = self.headers.get("Content-Type") or ""
            if "json" in kind:
                return json.loads(raw or b"{}")
            return dict(urllib.parse.parse_qsl(raw.decode())) if raw else {}

        def send(self, status, payload=None, location=None):
            self.send_response(status)
            if location:
                self.send_header("Location", location)
            if payload is not None:
                data = json.dumps(payload).encode()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_header("Content-Length", "0")
                self.end_headers()

        def do_GET(self):
            path, _, query = self.path.partition("?")
            if path == "/api/status":
                return self.send(204)
            if path == "/oidc/auth":
                assert "client_id=admin-console" in query
                return self.send(303, location="/sign-in")
            if path.startswith("/oidc/auth/"):
                if not state.consented:
                    return self.send(
                        303, location="http://localhost:8442/consent?app_id=admin-console"
                    )
                return self.send(
                    303, location="http://localhost:8442/console/callback?code=thecode"
                )
            if path == "/api/resources":
                return self.send(200, list(state.resources.values()))
            if path.startswith("/api/resources/") and path.endswith("/scopes"):
                rid = path.split("/")[3]
                return self.send(200, [s for s in state.scopes.values() if s["resourceId"] == rid])
            if path == "/oidc/.well-known/openid-configuration":
                return self.send(200, {"jwks_uri": "http://localhost:8441/oidc/jwks"})
            if path == "/oidc/jwks":
                return self.send(200, state.issuer.jwks())
            if path == "/api/applications":
                return self.send(200, list(state.applications.values()))
            if path == "/api/users":
                params = urllib.parse.parse_qs(query)
                wanted = {
                    field: params[f"search.{field}"][0]
                    for field in ("primaryEmail", "username")
                    if f"search.{field}" in params
                }
                return self.send(
                    200,
                    [
                        u
                        for u in state.tenant_users.values()
                        if all(u[field] == value for field, value in wanted.items())
                    ],
                )
            if re.fullmatch(r"/api/users/[^/]+/roles", path):
                uid = path.split("/")[3]
                return self.send(200, [state.roles[r] for r in state.user_roles.get(uid, [])])
            if path == "/api/roles":
                return self.send(200, list(state.roles.values()))
            if path.startswith("/api/roles/") and path.endswith("/scopes"):
                rid = path.split("/")[3]
                return self.send(200, [state.scopes[s] for s in state.role_scopes.get(rid, [])])
            self.send(404, {"path": path})

        def do_PATCH(self):
            body = self.body()
            if self.path.startswith("/api/applications/"):
                app = state.applications[self.path.split("/")[3]]
                app.setdefault("customClientMetadata", {}).update(body["customClientMetadata"])
                state.writes.append(("application-patch", app["name"]))
                return self.send(200, app)
            if self.path.startswith("/api/users/"):
                user = state.tenant_users[self.path.split("/")[3]]
                user.update(body)
                state.writes.append(("user-patch", user["username"]))
                return self.send(200, user)
            self.send(404)

        def do_PUT(self):
            if self.path == "/api/experience":
                return self.send(204)
            if self.path == "/api/configs/jwt-customizer/access-token":
                state.customizer = self.body()
                state.writes.append(("customizer",))
                return self.send(200, state.customizer)
            self.send(404)

        def do_POST(self):
            path = self.path
            body = self.body()
            if path == "/api/experience/verification/new-password-identity":
                name = body["identifier"]["value"]
                if name in state.users:
                    return self.send(422, {"code": "user.username_already_in_use"})
                state.pending = (name, body["password"])
                return self.send(200, {"verificationId": "v1"})
            if path == "/api/experience/verification/password":
                name = body["identifier"]["value"]
                if state.users.get(name) != body["password"]:
                    return self.send(422, {"code": "session.invalid_credentials"})
                return self.send(200, {"verificationId": "v2"})
            if path == "/api/experience/identification":
                return self.send(201)
            if path == "/api/experience/submit":
                if getattr(state, "pending", None):
                    name, password = state.pending
                    state.users[name] = password
                    state.writes.append(("user", name))
                    state.pending = None
                return self.send(200, {"redirectTo": "http://localhost:8442/oidc/auth/abc"})
            if path == "/api/interaction/consent":
                state.consented = True
                return self.send(200, {"redirectTo": "http://localhost:8442/oidc/auth/abc"})
            if path == "/oidc/token" and body["grant_type"] == "authorization_code":
                assert body["code"] == "thecode" and body["code_verifier"]
                return self.send(200, {"access_token": "mgmt-token", "token_type": "Bearer"})
            if path == "/oidc/token":
                assert body["grant_type"].endswith("token-exchange")
                assert body["client_id"] in state.applications
                uid = body["subject_token"].removeprefix("subject-for-")
                token = state.token_for(state.tenant_users[uid], body["resource"], body["scope"])
                return self.send(200, {"access_token": token, "token_type": "Bearer"})
            if self.headers.get("Authorization") != "Bearer mgmt-token":
                return self.send(401, {"code": "auth.unauthorized"})
            if path == "/api/resources":
                rid = state.new_id()
                state.resources[rid] = {"id": rid, **body, "scopes": []}
                state.writes.append(("resource", body["indicator"]))
                return self.send(201, state.resources[rid])
            if path.startswith("/api/resources/") and path.endswith("/scopes"):
                rid = path.split("/")[3]
                sid = state.new_id()
                state.scopes[sid] = {"id": sid, "resourceId": rid, **body}
                state.writes.append(("scope", body["name"]))
                return self.send(201, state.scopes[sid])
            if path == "/api/roles":
                rid = state.new_id()
                state.roles[rid] = {"id": rid, "name": body["name"], "type": body["type"]}
                state.role_scopes[rid] = list(body["scopeIds"])
                state.writes.append(("role", body["name"]))
                return self.send(201, state.roles[rid])
            if path.startswith("/api/roles/") and path.endswith("/scopes"):
                rid = path.split("/")[3]
                state.role_scopes[rid] += body["scopeIds"]
                state.writes.append(("role-scopes", state.roles[rid]["name"]))
                return self.send(201, {"scopeIds": body["scopeIds"]})
            if path == "/api/applications":
                aid = state.new_id()
                state.applications[aid] = {"id": aid, **body}
                state.writes.append(("application", body["name"]))
                return self.send(201, state.applications[aid])
            if path == "/api/users":
                uid = state.new_id()
                state.tenant_users[uid] = {"id": uid, **body}
                state.writes.append(("tenant-user", body["username"]))
                return self.send(201, state.tenant_users[uid])
            if re.fullmatch(r"/api/users/[^/]+/roles", path):
                uid = path.split("/")[3]
                state.user_roles.setdefault(uid, []).extend(body["roleIds"])
                state.writes.append(("user-roles", state.tenant_users[uid]["username"]))
                return self.send(201, {"roleIds": body["roleIds"]})
            if path == "/api/subject-tokens":
                return self.send(
                    201, {"subjectToken": f"subject-for-{body['userId']}", "expiresIn": 600}
                )
            self.send(404, {"path": path})

    return Handler


@pytest.fixture
def fake_logto():
    state = FakeLogto()
    server = HTTPServer(("127.0.0.1", 0), make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_port}"
    yield state
    server.shutdown()


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

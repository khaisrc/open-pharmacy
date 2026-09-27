"""Seed the local identity provider (Logto): its first admin, and everything
this API needs from it.

Local only, like seed_local, and idempotent: a re-run finds what exists and
changes nothing. Run by the one-shot `auth-seed` Compose service once the
`auth` service is healthy. What it does, in order:

1. The console's first admin, from AUTH_ADMIN_USERNAME / AUTH_ADMIN_PASSWORD.
   Logto has no setting for this: its first admin is an ordinary registration
   in the admin tenant, open only while that tenant has no users, so this is
   that registration over the experience API the Welcome page uses.
2. A Management API token, by signing that admin in the way the console
   does (authorization code with PKCE for the `admin-console` client,
   consent included) and asking for the default tenant's Management API
   as the resource.
3. Under the default tenant: the API resource (AUTH_AUDIENCE) with every
   scope in pharmacy.auth.scopes; the roles and the scopes ROLE_SCOPES
   grants each; the access-token JWT customizer that makes Logto emit the
   identity claims contract; an application for the UI.
4. The test users in TEST_USERS -- pharmacists, technicians, interns and
   admins employed by the home pharmacy -- with their roles and custom data.
5. A check: a real token for every test user, obtained the way a test would
   (subject token, then token exchange), verified against the instance and
   run through the same reading the API gives a request, so a user whose
   token would be refused is a seed failure, not a surprise at sign-in.

Two Logto facts this works around: the admin tenant is selected by the
request's Host header matching ADMIN_ENDPOINT (so every call carries the
public host while travelling to the internal address), and the admin
tenant's password policy consults api.pwnedpasswords.com unless the
database was seeded with `--dapc`, which the Compose service passes.
"""

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from pharmacy import services
from pharmacy.auth import Principal
from pharmacy.auth import claims as c
from pharmacy.auth.providers.logto import LogtoProvider
from pharmacy.auth.scopes import ROLE_SCOPES, Scope
from pharmacy.auth.verifier import TokenVerifier
from pharmacy.exceptions import Unauthenticated
from pharmacy.models import PharmacyLocation

MANAGEMENT_API = "https://default.logto.app/api"
ACCESS_TOKEN_TTL_SECONDS = 900
UI_APPLICATION = "open-pharmacy-ui"
UI_REDIRECT = "http://localhost:3000/callback"

# The people a local stack can sign in as. Every one is employed by the
# home pharmacy (admins by none), shares TEST_PASSWORD, and has the email
# <given>.<family>@open-pharmacy.local -- the username with its underscore
# as a dot, since Logto usernames allow no dots.
# docs/identity-providers/test-users.md lists them; a test keeps the two in
# step. Their registrations and certifications are their own, none of
# seed_local's: a token whose registration an active row already holds
# retires that row as its predecessor, so a shared number would quietly
# deactivate the demo pharmacist the first time Elena signed in.
TEST_PASSWORD = "Passw0rd!"
TEST_USERS = [
    # username, given name, family name, roles, registration, certification, supervisor
    ("elena_ortiz", "Elena", "Ortiz", ["pharmacist"], ("NY", "RPH.140001"), None, None),
    ("brendan_walsh", "Brendan", "Walsh", ["pharmacist"], ("NY", "RPH.140002"), None, None),
    *[
        (u, g, f, ["certified_technician"], ("NY", r), ("PTCB", n), None)
        for u, g, f, r, n in [
            ("soojin_kim", "Soo-jin", "Kim", "RPT.240001", "20240001"),
            ("marcus_bell", "Marcus", "Bell", "RPT.240002", "20240002"),
            ("priya_natarajan", "Priya", "Natarajan", "RPT.240003", "20240003"),
            ("tomas_reyes", "Tomás", "Reyes", "RPT.240004", "20240004"),
            ("hannah_oconnell", "Hannah", "O'Connell", "RPT.240005", "20240005"),
        ]
    ],
    *[
        (u, g, f, ["technician"], None, None, None)
        for u, g, f in [
            ("marco_diaz", "Marco", "Diaz"),
            ("aisha_rahman", "Aisha", "Rahman"),
            ("liam_chen", "Liam", "Chen"),
            ("grace_okafor", "Grace", "Okafor"),
            ("noah_fischer", "Noah", "Fischer"),
        ]
    ],
    ("sam_whitaker", "Sam", "Whitaker", ["intern"], None, None, "elena_ortiz"),
    ("jade_moreau", "Jade", "Moreau", ["intern"], None, None, "brendan_walsh"),
    ("ada_lovelace", "Ada", "Lovelace", ["admin"], None, None, None),
    ("grace_hopper", "Grace", "Hopper", ["admin"], None, None, None),
]


def email_for(username):
    return f"{username.replace('_', '.')}@open-pharmacy.local"


# The access-token JWT customizer: how Logto puts the identity claims
# contract into a token. `name` and the profile names are Logto's own user
# fields; `customData` holds the rest. Logto issues a token even when this
# throws, which is why the API refuses a token without an email itself.
CUSTOMIZER = f"""const getCustomJwtClaims = async ({{ token, context }}) => {{
  const user = context?.user;
  if (!user) {{ return {{}}; }}
  return {{
    {c.EMAIL}: user.primaryEmail,
    email_verified: Boolean(user.primaryEmailVerified),
    {c.NAME}: user.name,
    {c.GIVEN_NAME}: user.profile?.givenName,
    {c.FAMILY_NAME}: user.profile?.familyName,
    {c.ROLES}: (user.roles ?? []).map((role) => role.name),
    {c.PHARMACY_ID}: user.customData?.pharmacy_id,
    {c.REGISTRATION_STATE}: user.customData?.registration_state,
    {c.REGISTRATION_NUMBER}: user.customData?.registration_number,
    {c.CERTIFICATION_BODY}: user.customData?.certification_body,
    {c.CERTIFICATION_NUMBER}: user.customData?.certification_number,
    {c.SUPERVISING_USER_ID}: user.customData?.supervising_user_id,
  }};
}};"""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Redirects are answers here, not instructions: the OIDC endpoint
    redirects to the sign-in page (the cookie is the point) and later to the
    console callback (the code in the URL is the point)."""

    def redirect_request(self, *args, **kwargs):
        return None


class Logto:
    """Just enough of Logto's HTTP surface, through its internal address
    with the public host in every request."""

    def __init__(self, *, internal_console, internal_api, public_console, public_endpoint):
        self.internal_console = internal_console.rstrip("/")
        self.internal_api = internal_api.rstrip("/")
        self.console = public_console.rstrip("/")
        self.endpoint = public_endpoint.rstrip("/")
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), NoRedirect()
        )

    def call(self, method, path, *, body=None, form=None, token=None, admin=True):
        """One request. `admin` selects the admin tenant (the console's),
        the alternative being the default tenant (the API's). Returns
        (status, parsed JSON or None, Location header or None)."""
        host = urllib.parse.urlsplit(self.console if admin else self.endpoint).netloc
        headers = {"Host": host}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = None
        if body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        elif form is not None:
            data = urllib.parse.urlencode(form).encode()
        base = self.internal_console if admin else self.internal_api
        request = urllib.request.Request(f"{base}{path}", data=data, method=method, headers=headers)
        try:
            with self.opener.open(request, timeout=30) as response:
                return response.status, _json(response), response.headers.get("Location")
        except urllib.error.HTTPError as error:
            return error.code, _json(error), error.headers.get("Location")

    @classmethod
    def from_settings(cls):
        """The local instance: the Compose `auth` service, reached over the
        Compose network with the public URLs the browser uses as the Host.
        ADMIN_ENDPOINT defaults to what Compose gives Logto itself, since
        the admin tenant is found by that host."""
        public_endpoint = settings.AUTH_ISSUER.removesuffix("/oidc")
        return cls(
            internal_console=os.environ.get("AUTH_INTERNAL_CONSOLE_URL", "http://auth:3002"),
            internal_api=settings.AUTH_INTERNAL_URL or public_endpoint,
            public_console=os.environ.get("ADMIN_ENDPOINT", "http://127.0.0.1:8442"),
            public_endpoint=public_endpoint,
        )

    def wait(self, attempts=60):
        for _ in range(attempts):
            try:
                status, _, _ = self.call("GET", "/api/status")
                if status in (200, 204):
                    return
            except (urllib.error.URLError, ConnectionError):
                pass
            time.sleep(2)
        raise CommandError(f"the identity provider at {self.internal_console} did not come up")

    # --- the admin ---------------------------------------------------------

    def start_interaction(self, *, resource=None):
        """Open an OIDC interaction for the console app, like the browser
        does; returns the PKCE verifier the code exchange needs."""
        verifier = secrets.token_urlsafe(32)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        query = {
            "client_id": "admin-console",
            "redirect_uri": f"{self.console}/console/callback",
            "response_type": "code",
            "scope": "openid profile all",
            "code_challenge": challenge.decode().rstrip("="),
            "code_challenge_method": "S256",
            "state": secrets.token_urlsafe(8),
            "prompt": "login",
        }
        if resource:
            query["resource"] = resource
        status, _, _ = self.call("GET", f"/oidc/auth?{urllib.parse.urlencode(query)}")
        if status not in (302, 303):
            raise CommandError(f"could not open a sign-in interaction: HTTP {status}")
        return verifier

    def register_admin(self, username, password):
        """True if created, False if the username already exists."""
        self.start_interaction()
        self.call("PUT", "/api/experience", body={"interactionEvent": "Register"})
        status, body, _ = self.call(
            "POST",
            "/api/experience/verification/new-password-identity",
            body={"identifier": {"type": "username", "value": username}, "password": password},
        )
        if (body or {}).get("code") == "user.username_already_in_use":
            return False
        if status != 200:
            raise CommandError(f"registration refused: HTTP {status} {body}")
        status, body, _ = self.identify(body["verificationId"])
        if status == 403:
            raise CommandError(
                f"an admin already exists under another username, so {username!r} cannot be "
                "created; sign in with the existing one or reset the auth database."
            )
        if status != 201:
            raise CommandError(f"identification failed: HTTP {status} {body}")
        status, body, _ = self.call("POST", "/api/experience/submit")
        if status != 200:
            raise CommandError(f"submit failed: HTTP {status} {body}")
        return True

    def management_token(self, username, password):
        """Sign the admin in as the console does and exchange the code for a
        token good for the default tenant's Management API."""
        verifier = self.start_interaction(resource=MANAGEMENT_API)
        self.call("PUT", "/api/experience", body={"interactionEvent": "SignIn"})
        status, body, _ = self.call(
            "POST",
            "/api/experience/verification/password",
            body={"identifier": {"type": "username", "value": username}, "password": password},
        )
        if status != 200:
            raise CommandError(f"the admin could not sign in: HTTP {status} {body}")
        self.identify(body["verificationId"])
        status, body, _ = self.call("POST", "/api/experience/submit")
        if status != 200:
            raise CommandError(f"sign-in submit failed: HTTP {status} {body}")
        location = self._follow(body["redirectTo"])
        if "/consent" in (location or ""):
            # First use of the Management API resource by this admin.
            status, body, _ = self.call("POST", "/api/interaction/consent", body={})
            if status != 200:
                raise CommandError(f"consent failed: HTTP {status} {body}")
            location = self._follow(body["redirectTo"])
        code = urllib.parse.parse_qs(urllib.parse.urlsplit(location or "").query).get("code")
        if not code:
            raise CommandError(f"no authorization code came back (landed on {location})")
        status, body, _ = self.call(
            "POST",
            "/oidc/token",
            form={
                "grant_type": "authorization_code",
                "client_id": "admin-console",
                "code": code[0],
                "redirect_uri": f"{self.console}/console/callback",
                "code_verifier": verifier,
                "resource": MANAGEMENT_API,
            },
        )
        if status != 200:
            raise CommandError(f"token exchange failed: HTTP {status} {body}")
        return body["access_token"]

    def identify(self, verification_id):
        return self.call(
            "POST", "/api/experience/identification", body={"verificationId": verification_id}
        )

    def _follow(self, url):
        """GET an OIDC continuation URL, returning where it redirects."""
        path = url[len(self.console) :] if url.startswith(self.console) else url
        _, _, location = self.call("GET", path)
        return location

    # --- the default tenant's Management API ----------------------------------

    def api(self, method, path, body=None, token=None):
        status, data, _ = self.call(method, f"/api{path}", body=body, token=token, admin=False)
        if status >= 400:
            raise CommandError(f"{method} /api{path}: HTTP {status} {data}")
        return data

    def user_token(self, user_id, app_id, audience, scopes, token):
        """An access token for `user_id`, the way a test gets one without a
        browser: a subject token from the Management API, exchanged at the
        token endpoint for this API's audience."""
        subject = self.api("POST", "/subject-tokens", {"userId": user_id}, token=token)
        status, body, _ = self.call(
            "POST",
            "/oidc/token",
            form={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "client_id": app_id,
                "subject_token": subject["subjectToken"],
                "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
                "resource": audience,
                "scope": " ".join(sorted(str(s) for s in scopes)),
            },
            admin=False,
        )
        if status != 200:
            raise CommandError(f"token exchange for {user_id} failed: HTTP {status} {body}")
        return body["access_token"]


def admin_credentials(command):
    """AUTH_ADMIN_USERNAME / AUTH_ADMIN_PASSWORD, refusing outside local: the
    commands that sign the console's admin in are for the Compose stack only."""
    env = os.environ.get("DJANGO_ENV", "local")
    if env != "local":
        raise CommandError(f"{command} refuses to run with DJANGO_ENV={env!r}.")
    password = os.environ.get("AUTH_ADMIN_PASSWORD")
    if not password:
        raise CommandError("AUTH_ADMIN_PASSWORD is not set.")
    return os.environ.get("AUTH_ADMIN_USERNAME", "admin"), password


def _json(response):
    if "json" not in (response.headers.get("Content-Type") or ""):
        return None
    text = response.read().decode()
    return json.loads(text) if text else None


class Command(BaseCommand):
    help = "Seed the local identity provider: first admin, API resource, scopes, roles, customizer."

    def handle(self, *args, **options):
        username, password = admin_credentials("seed_auth")
        logto = Logto.from_settings()
        logto.wait()

        if logto.register_admin(username, password):
            self.stdout.write(self.style.SUCCESS(f"Created admin user {username!r}."))
        else:
            self.stdout.write(f"Admin user {username!r} already exists.")

        token = logto.management_token(username, password)
        resource = self._ensure_resource(logto, token)
        scopes = self._ensure_scopes(logto, token, resource)
        roles = self._ensure_roles(logto, token, scopes)
        self._ensure_customizer(logto, token)
        app = self._ensure_ui_application(logto, token)
        users = self._ensure_test_users(logto, token, roles)
        self._verify_tokens(logto, token, app, users)
        self.stdout.write(
            self.style.SUCCESS(
                f"Identity provider ready: resource {settings.AUTH_AUDIENCE}, "
                f"{len(scopes)} scopes, {len(ROLE_SCOPES)} roles, customizer set, "
                f"{len(users)} test users (password {TEST_PASSWORD!r}), every token checked. "
                f"Console: {logto.console}/console"
            )
        )

    def _ensure_resource(self, logto, token):
        for resource in logto.api("GET", "/resources", token=token):
            if resource["indicator"] == settings.AUTH_AUDIENCE:
                return resource
        return logto.api(
            "POST",
            "/resources",
            {
                "name": "open-pharmacy API",
                "indicator": settings.AUTH_AUDIENCE,
                "accessTokenTtl": ACCESS_TOKEN_TTL_SECONDS,
            },
            token=token,
        )

    def _ensure_scopes(self, logto, token, resource):
        path = f"/resources/{resource['id']}/scopes"
        existing = {
            s["name"]: s["id"] for s in logto.api("GET", f"{path}?page_size=100", token=token)
        }
        for scope in Scope:
            if scope not in existing:
                created = logto.api(
                    "POST", path, {"name": str(scope), "description": str(scope)}, token=token
                )
                existing[str(scope)] = created["id"]
        return existing

    def _ensure_roles(self, logto, token, scopes):
        """{role name: id}, creating what is missing and topping up grants."""
        listed = logto.api("GET", "/roles?page_size=100", token=token)
        existing = {r["name"]: r["id"] for r in listed}
        for name, wanted in ROLE_SCOPES.items():
            ids = [scopes[str(s)] for s in wanted]
            if name not in existing:
                created = logto.api(
                    "POST",
                    "/roles",
                    {"name": name, "description": name, "type": "User", "scopeIds": ids},
                    token=token,
                )
                existing[name] = created["id"]
                continue
            role_path = f"/roles/{existing[name]}/scopes"
            held = {s["id"] for s in logto.api("GET", f"{role_path}?page_size=100", token=token)}
            missing = [i for i in ids if i not in held]
            if missing:
                logto.api("POST", role_path, {"scopeIds": missing}, token=token)
        return existing

    def _ensure_customizer(self, logto, token):
        logto.api(
            "PUT",
            "/configs/jwt-customizer/access-token",
            {"script": CUSTOMIZER, "blockIssuanceOnError": True},
            token=token,
        )

    def _ensure_ui_application(self, logto, token):
        """The single-page application the UI signs in with. Token exchange
        is enabled on it for the check below (and the e2e suite), which
        needs a token for a user without a browser."""
        for app in logto.api("GET", "/applications?page_size=100", token=token):
            if app["name"] == UI_APPLICATION:
                if not (app.get("customClientMetadata") or {}).get("allowTokenExchange"):
                    logto.api(
                        "PATCH",
                        f"/applications/{app['id']}",
                        {"customClientMetadata": {"allowTokenExchange": True}},
                        token=token,
                    )
                return app
        return logto.api(
            "POST",
            "/applications",
            {
                "name": UI_APPLICATION,
                "type": "SPA",
                "oidcClientMetadata": {
                    "redirectUris": [UI_REDIRECT],
                    "postLogoutRedirectUris": [UI_REDIRECT.rsplit("/", 1)[0]],
                },
                "customClientMetadata": {"allowTokenExchange": True},
            },
            token=token,
        )

    def _home_pharmacy_id(self):
        location = PharmacyLocation.objects.filter(
            ncpdp_id=settings.HOME_PHARMACY_LOCATION_NCPDP_ID
        ).first()
        if location is None:
            raise CommandError(
                "The home pharmacy location is not in the database; run migrate first."
            )
        return str(location.pharmacy_id)

    def _ensure_test_users(self, logto, token, roles):
        """{username: Logto user} for TEST_USERS, created or found by email,
        with their roles and custom data as listed. Supervisors are listed
        before the interns they supervise, so the id is known by then."""
        pharmacy_id = self._home_pharmacy_id()
        users = {}
        for (
            username,
            given,
            family,
            user_roles,
            registration,
            certification,
            supervisor,
        ) in TEST_USERS:
            email = email_for(username)
            custom = {}
            if "admin" not in user_roles:
                custom[c.PHARMACY_ID] = pharmacy_id
            if registration:
                custom[c.REGISTRATION_STATE], custom[c.REGISTRATION_NUMBER] = registration
            if certification:
                custom[c.CERTIFICATION_BODY], custom[c.CERTIFICATION_NUMBER] = certification
            if supervisor:
                custom[c.SUPERVISING_USER_ID] = users[supervisor]["id"]
            profile = {
                "username": username,
                "primaryEmail": email,
                "name": f"{given} {family}",
                "profile": {"givenName": given, "familyName": family},
                "customData": custom,
            }
            found = logto.api(
                "GET",
                f"/users?search.primaryEmail={urllib.parse.quote(email)}&mode=exact",
                token=token,
            )
            if found:
                user = found[0]
                if user.get("customData") != custom or user.get("name") != profile["name"]:
                    user = logto.api("PATCH", f"/users/{user['id']}", profile, token=token)
            else:
                user = logto.api(
                    "POST", "/users", {**profile, "password": TEST_PASSWORD}, token=token
                )
                self.stdout.write(f"Created test user {username!r} ({', '.join(user_roles)}).")
            held = {r["name"] for r in logto.api("GET", f"/users/{user['id']}/roles", token=token)}
            missing = [roles[r] for r in user_roles if r not in held]
            if missing:
                logto.api("POST", f"/users/{user['id']}/roles", {"roleIds": missing}, token=token)
            users[username] = user
        return users

    def _verify_tokens(self, logto, token, app, users):
        """Mint a token per test user and read it the way the API would.

        Verified against the instance through the adapter (discovery, keys),
        mapped through the adapter's claims(), and run through the staff
        service's reading without writing: the same path a request takes up
        to the point of creating the row.
        """
        provider = LogtoProvider(
            issuer=settings.AUTH_ISSUER,
            audience=settings.AUTH_AUDIENCE,
            internal_url=settings.AUTH_INTERNAL_URL,
        )
        verifier = TokenVerifier(provider=provider)
        failures = []
        for username, _, _, user_roles, *_ in TEST_USERS:
            expected = set()
            for role in user_roles:
                expected |= ROLE_SCOPES[role]
            raw = logto.user_token(
                users[username]["id"], app["id"], settings.AUTH_AUDIENCE, expected, token
            )
            try:
                claims = provider.claims(verifier.verify(raw))
                principal = Principal.from_claims(claims)
                if not principal.email:
                    raise Unauthenticated("no email claim", code="token_without_email")
                services.staff.describe(principal)
                if principal.scopes != {str(s) for s in expected}:
                    raise Unauthenticated(
                        f"scopes {sorted(principal.scopes)} != {sorted(str(s) for s in expected)}",
                        code="scopes",
                    )
            except Exception as exc:  # noqa: BLE001 -- every failure is reported, then raised once
                failures.append(f"{username}: {getattr(exc, 'detail', exc)}")
                continue
            self.stdout.write(f"  token ok  {username:<18} {principal.role:<22} {principal.email}")
        if failures:
            raise CommandError("tokens the API would refuse:\n  " + "\n  ".join(failures))

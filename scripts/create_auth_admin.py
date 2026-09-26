"""Create the identity server's first admin, so a fresh stack has a console login.

Logto has no setting for this: its first admin is made on the console's
Welcome page, which is an ordinary registration in the admin tenant, open
only while that tenant has no users. This does the same thing over the
same API the page uses -- start an OIDC interaction for the console app,
register a username and password, submit -- and is idempotent: a second
run finds the username taken and says so.

Local only. The credentials come from AUTH_ADMIN_USERNAME and
AUTH_ADMIN_PASSWORD in .env.local, throwaways like everything there.
Standard library only, so it runs in the web image as a one-shot Compose
service (`auth-seed`).
"""

import base64
import hashlib
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar


def env(name, default=None):
    value = os.environ.get(name, default)
    if value is None:
        sys.exit(f"{name} is not set")
    return value


# Where this script reaches the identity server, and the public URL of its
# admin console. Logto picks the admin tenant by the request's Host header
# matching ADMIN_ENDPOINT, so every call carries the public host even though
# it travels to the internal address; the console app's redirect URI must
# match the public URL too.
INTERNAL = env("AUTH_INTERNAL_ADMIN_ENDPOINT", "http://auth:3002").rstrip("/")
PUBLIC = env("ADMIN_ENDPOINT", "http://localhost:8442").rstrip("/")
PUBLIC_HOST = urllib.parse.urlsplit(PUBLIC).netloc
USERNAME = env("AUTH_ADMIN_USERNAME", "admin")
PASSWORD = env("AUTH_ADMIN_PASSWORD")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """The OIDC endpoint answers with a redirect to the sign-in page; the
    cookie it sets is the point, the page is not. Passing a subclass to
    build_opener replaces the default handler rather than adding to it."""

    def redirect_request(self, *args, **kwargs):
        return None


jar = CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), NoRedirect())


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Host": PUBLIC_HOST}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(f"{INTERNAL}{path}", data=data, method=method, headers=headers)
    try:
        with opener.open(request, timeout=30) as response:
            return response.status, parse(response)
    except urllib.error.HTTPError as error:
        # A redirect lands here too (they are not followed); its body is
        # not JSON and does not matter.
        return error.code, parse(error)


def parse(response):
    if "json" not in (response.headers.get("Content-Type") or ""):
        return None
    text = response.read().decode()
    return json.loads(text) if text else None


def wait_for_logto():
    for _ in range(60):
        try:
            status, _ = call("GET", "/api/status")
            if status in (200, 204):
                return
        except (urllib.error.URLError, ConnectionError):
            pass
        time.sleep(2)
    sys.exit(f"the identity server at {INTERNAL} did not come up")


def main():
    wait_for_logto()
    verifier = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    query = urllib.parse.urlencode(
        {
            "client_id": "admin-console",
            "redirect_uri": f"{PUBLIC}/console/callback",
            "response_type": "code",
            "scope": "openid profile",
            "code_challenge": challenge.decode().rstrip("="),
            "code_challenge_method": "S256",
            "state": secrets.token_urlsafe(8),
            "prompt": "login",
        }
    )
    status, _ = call("GET", f"/oidc/auth?{query}")
    if status not in (302, 303):
        sys.exit(f"could not open a sign-in interaction: HTTP {status}")
    call("PUT", "/api/experience", {"interactionEvent": "Register"})
    status, body = call(
        "POST",
        "/api/experience/verification/new-password-identity",
        {"identifier": {"type": "username", "value": USERNAME}, "password": PASSWORD},
    )
    code = (body or {}).get("code", "")
    if code == "user.username_already_in_use":
        print(f"admin user {USERNAME!r} already exists; nothing to do")
        return
    if status != 200:
        sys.exit(f"registration refused: HTTP {status} {body}")
    status, body = call(
        "POST", "/api/experience/identification", {"verificationId": body["verificationId"]}
    )
    if status == 403:
        # Registration in the admin tenant is open only until it has a user.
        sys.exit(
            f"an admin already exists under another username, so {USERNAME!r} cannot be "
            "created; sign in with the existing one or reset the auth database."
        )
    if status != 201:
        sys.exit(f"identification failed: HTTP {status} {body}")
    status, body = call("POST", "/api/experience/submit")
    if status != 200:
        sys.exit(f"submit failed: HTTP {status} {body}")
    print(f"created admin user {USERNAME!r}; sign in at {PUBLIC}/console")


if __name__ == "__main__":
    main()

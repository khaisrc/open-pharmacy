"""A real access token from the local identity provider, for curl.

Where `mint_token` signs a token with the dev key for any identity you
describe, this asks the Compose Logto for one: the user must exist there
(the seeded test users in docs/identity-providers/test-users.md, or anyone
created in the console), and the token is exactly what that user would get
by signing in to the UI -- their own subject, their claims, the scopes
their roles grant. No sign-in flow: the console's admin gets a subject
token for the user from the Management API and exchanges it for this API's
audience at the UI application, the way `seed_auth` checks its users.

Only the token goes to stdout, so it can be captured:

    TOKEN=$(docker compose exec -T web python manage.py auth_token elena_ortiz)

Local only, like the seed: it signs in as AUTH_ADMIN_USERNAME.
"""

import urllib.parse

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from pharmacy.auth.scopes import ROLE_SCOPES

from .seed_auth import UI_APPLICATION, Logto, admin_credentials


class Command(BaseCommand):
    help = "Get an access token for a user of the local identity provider, by username."

    def add_arguments(self, parser):
        parser.add_argument("username", help="A Logto username, e.g. elena_ortiz.")
        parser.add_argument(
            "--scopes",
            nargs="*",
            help="Ask for these scopes only; defaults to everything the user's roles grant.",
        )

    def handle(self, *args, **options):
        admin, password = admin_credentials("auth_token")
        logto = Logto.from_settings()
        try:
            # A few seconds, not the seed's two minutes: here the likely
            # cause is a stack started without the `auth` profile.
            logto.wait(attempts=3)
        except CommandError as exc:
            raise CommandError(
                f"{exc}; start it with `make start` (or `docker compose --profile auth up`)."
            ) from exc

        token = logto.management_token(admin, password)
        username = options["username"]
        found = logto.api(
            "GET",
            f"/users?search.username={urllib.parse.quote(username)}&mode=exact",
            token=token,
        )
        if not found:
            raise CommandError(
                f"no user {username!r} at the identity provider; the seeded ones are in "
                "docs/identity-providers/test-users.md (run `seed_auth` to create them)."
            )
        user = found[0]
        apps = logto.api("GET", "/applications", token=token)
        app = next((a for a in apps if a["name"] == UI_APPLICATION), None)
        if app is None:
            raise CommandError(f"no {UI_APPLICATION!r} application; run `seed_auth` first.")

        scopes = options["scopes"]
        if scopes is None:
            scopes = set()
            for role in logto.api("GET", f"/users/{user['id']}/roles", token=token):
                scopes |= {str(s) for s in ROLE_SCOPES.get(role["name"], ())}
        self.stdout.write(
            logto.user_token(user["id"], app["id"], settings.AUTH_AUDIENCE, scopes, token)
        )

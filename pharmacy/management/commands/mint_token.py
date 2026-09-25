"""A dev access token, for curl without a UI.

Signs a token with LOGTO_DEV_SIGNING_KEY -- a PEM private key only local.py
reads -- carrying the same claims Logto's JWT customizer emits, so the API
treats it exactly as a Logto token. Two guards, like seed_local: the
command refuses outside DJANGO_ENV=local, and the verifier only accepts the
dev key when that setting is non-empty, which no other settings module
makes it.

    python manage.py mint_token --generate-key        # once; paste into .env.local
    python manage.py mint_token --email rph@example.local --roles pharmacist \\
        --pharmacy <pharmacy uuid> --registration NY RPH.023417
"""

import os
import time
import uuid

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from pharmacy.auth.scopes import ROLE_SCOPES
from pharmacy.auth.verifier import DEV_KID


class Command(BaseCommand):
    help = "Mint a Logto-shaped access token signed with the local dev key."

    def add_arguments(self, parser):
        parser.add_argument("--generate-key", action="store_true", help="Print a new PEM key.")
        parser.add_argument("--email")
        parser.add_argument("--sub", help="The subject; derived from the email if omitted.")
        parser.add_argument("--given-name", default="Dev")
        parser.add_argument("--family-name", default="User")
        parser.add_argument("--roles", nargs="+", default=["pharmacist"])
        parser.add_argument("--pharmacy", help="pharmacy_id claim (a Pharmacy UUID).")
        parser.add_argument(
            "--registration", nargs=2, metavar=("STATE", "NUMBER"), help="Board registration."
        )
        parser.add_argument("--supervisor", help="supervising_sub, for an intern.")
        parser.add_argument("--scopes", nargs="*", help="Defaults to the roles' scopes.")
        parser.add_argument("--ttl", type=int, default=3600, help="Seconds until expiry.")

    def handle(self, *args, **options):
        env = os.environ.get("DJANGO_ENV", "local")
        if env != "local":
            raise CommandError(f"mint_token refuses to run with DJANGO_ENV={env!r}.")
        if options["generate_key"]:
            key = ec.generate_private_key(ec.SECP384R1())
            pem = key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ).decode()
            self.stdout.write(
                "Put this in .env.local as LOGTO_DEV_SIGNING_KEY (quote it; keep the newlines):\n"
            )
            self.stdout.write(pem)
            return
        pem = settings.LOGTO_DEV_SIGNING_KEY
        if not pem:
            raise CommandError(
                "LOGTO_DEV_SIGNING_KEY is not set; run `mint_token --generate-key` first."
            )
        if not options["email"]:
            raise CommandError("--email is required.")
        key = serialization.load_pem_private_key(pem.replace("\\n", "\n").encode(), password=None)
        roles = options["roles"]
        scopes = options["scopes"]
        if scopes is None:
            scopes = set()
            for role in roles:
                scopes |= ROLE_SCOPES.get(role, frozenset())
        now = int(time.time())
        claims = {
            "jti": uuid.uuid4().hex,
            "sub": options["sub"]
            or "dev-" + uuid.uuid5(uuid.NAMESPACE_URL, options["email"]).hex[:12],
            "iat": now,
            "exp": now + options["ttl"],
            "iss": f"{settings.LOGTO_ENDPOINT.rstrip('/')}/oidc",
            "aud": settings.LOGTO_API_RESOURCE,
            "client_id": "mint_token",
            "scope": " ".join(sorted(str(s) for s in scopes)),
            "email": options["email"],
            "email_verified": True,
            "name": f"{options['given_name']} {options['family_name']}",
            "given_name": options["given_name"],
            "family_name": options["family_name"],
            "roles": roles,
            "pharmacy_id": options["pharmacy"],
            "supervising_sub": options["supervisor"],
        }
        if options["registration"]:
            claims["registration_state"], claims["registration_number"] = options["registration"]
        self.stdout.write(jwt.encode(claims, key, algorithm="ES384", headers={"kid": DEV_KID}))

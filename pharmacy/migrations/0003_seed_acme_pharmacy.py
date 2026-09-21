"""Seed the pharmacy this service is the backend for.

The deployment serves one real organization -- Acme Pharmacy, a single retail
location -- so that row is reference data rather than something a client
creates. Seeding it in a migration puts it in every database the app runs
against, including the one pytest builds, which is what lets tests share a
dispensing site instead of each minting its own.

Identifiers are made up but well-formed: the NPI carries a valid check digit
and the NCPDP ID is already normalized to 7 digits, because a data migration
uses historical models and so runs neither field validators nor the custom
save() that would otherwise normalize them.

get_or_create keys on the values that must be unique, so re-running against a
database that already holds Acme is a no-op rather than a constraint error.
"""

from django.db import migrations

PHARMACY_NAME = "Acme Pharmacy"
LOCATION_NCPDP_ID = "4417829"


def seed_acme(apps, schema_editor):
    Address = apps.get_model("pharmacy", "Address")
    Pharmacy = apps.get_model("pharmacy", "Pharmacy")
    PharmacyLocation = apps.get_model("pharmacy", "PharmacyLocation")

    address, _ = Address.objects.get_or_create(
        line1="1450 SW Morrison Street",
        city="Portland",
        state="OR",
        postal_code="97205",
        defaults={"line2": "Suite 200", "country": "US"},
    )

    pharmacy, _ = Pharmacy.objects.get_or_create(
        name=PHARMACY_NAME,
        defaults={
            "legal_name": "Acme Pharmacy, LLC",
            "phone_number": "+15035550142",
            "email": "contact@acmepharmacy.example",
            "website": "https://acmepharmacy.example",
        },
    )

    PharmacyLocation.objects.get_or_create(
        ncpdp_id=LOCATION_NCPDP_ID,
        defaults={
            "pharmacy": pharmacy,
            "store_number": "1",
            "npi": "1938756214",
            "dea_number": "AA1234563",
            "location_type": "retail",
            "phone_number": "+15035550142",
            "fax_number": "+15035550143",
            "email": "store1@acmepharmacy.example",
            "address": address,
            "is_24_hour": False,
        },
    )


def unseed_acme(apps, schema_editor):
    """Remove the seed, but never a location that has dispensed anything."""
    Pharmacy = apps.get_model("pharmacy", "Pharmacy")
    PharmacyLocation = apps.get_model("pharmacy", "PharmacyLocation")

    location = PharmacyLocation.objects.filter(ncpdp_id=LOCATION_NCPDP_ID).first()
    if location is None:
        return
    if location.prescriptions.exists():
        return
    address_id = location.address_id
    pharmacy_id = location.pharmacy_id
    location.delete()

    pharmacy = Pharmacy.objects.filter(pk=pharmacy_id).first()
    if pharmacy is not None and not pharmacy.locations.exists():
        pharmacy.delete()
        apps.get_model("pharmacy", "Address").objects.filter(pk=address_id).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("pharmacy", "0002_alter_prescription_status"),
    ]

    operations = [
        migrations.RunPython(seed_acme, unseed_acme),
    ]

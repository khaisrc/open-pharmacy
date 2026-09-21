"""Make a prescription's dispensing location required.

Every prescription this service holds is dispensed at the pharmacy it backs, so
a null location described a state that can no longer occur. Rows written before
the API began filling the location in are backfilled to the configured home
location first -- the column cannot be made NOT NULL while any remain.

The backfill is not reversible: once a row points at the home location it is
indistinguishable from one written there, so the backwards path only restores
the nullable column.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def backfill_home_location(apps, schema_editor):
    Prescription = apps.get_model("pharmacy", "Prescription")
    PharmacyLocation = apps.get_model("pharmacy", "PharmacyLocation")

    # _base_manager throughout: a soft-deleted prescription still occupies the
    # column, so leaving one null would fail the NOT NULL alter below.
    unrouted = Prescription._base_manager.filter(pharmacy_location__isnull=True)
    if not unrouted.exists():
        return

    ncpdp_id = settings.HOME_PHARMACY_LOCATION_NCPDP_ID
    home = PharmacyLocation._base_manager.filter(ncpdp_id=ncpdp_id).first()
    if home is None:
        raise RuntimeError(
            f"Cannot backfill {unrouted.count()} prescription(s) with no pharmacy "
            f"location: no location has the configured home NCPDP ID {ncpdp_id!r}. "
            "Seed it, or point HOME_PHARMACY_LOCATION_NCPDP_ID at an existing one."
        )
    unrouted.update(pharmacy_location=home)


class Migration(migrations.Migration):

    dependencies = [
        ("pharmacy", "0003_seed_acme_pharmacy"),
    ]

    operations = [
        migrations.RunPython(backfill_home_location, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="prescription",
            name="pharmacy_location",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="prescriptions",
                to="pharmacy.pharmacylocation",
            ),
        ),
    ]

"""Drop the `draft` prescription status.

A prescription is written to be dispensed, so it is `active` from the moment it
exists; there is no stage at which one sits unusable in the system. Existing
draft rows are promoted rather than deleted -- they are real prescriptions that
were only ever waiting on a PATCH that the API no longer requires.

The promotion is not reversible: once a draft reads as `active` it is
indistinguishable from one written active, so the backwards path is a no-op and
restoring the old state means restoring from a backup.
"""

from django.db import migrations, models


def promote_drafts(apps, schema_editor):
    Prescription = apps.get_model("pharmacy", "Prescription")
    # _base_manager, not objects: soft-deleted drafts still carry the status and
    # would otherwise keep a value the choices no longer contain.
    Prescription._base_manager.filter(status="draft").update(status="active")


class Migration(migrations.Migration):

    dependencies = [
        ("pharmacy", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(promote_drafts, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="prescription",
            name="status",
            field=models.CharField(
                choices=[
                    ("active", "Active"),
                    ("on_hold", "On hold"),
                    ("completed", "Completed"),
                    ("cancelled", "Cancelled"),
                    ("expired", "Expired"),
                ],
                default="active",
                max_length=16,
            ),
        ),
    ]

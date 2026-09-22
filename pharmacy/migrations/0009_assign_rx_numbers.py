# Numbers any prescription already in a database -- a development one; the
# service has not been to production -- so rx_number can become non-null.
# Arithmetic on rows that exist: per pharmacy, in written_at order, from 1.
# No RxCounter row is created or touched. Seeding the counter is part of
# onboarding a pharmacy, at deployment time, and must set last_issued at or
# above the highest number assigned here.

from django.db import migrations, models


def assign_rx_numbers(apps, schema_editor):
    Prescription = apps.get_model("pharmacy", "Prescription")
    unnumbered = (
        Prescription.objects.filter(rx_number__isnull=True)
        .select_related("pharmacy_location")
        .order_by("written_at", "created_at")
    )
    next_number = {}
    for prescription in unnumbered:
        pharmacy_id = prescription.pharmacy_location.pharmacy_id
        next_number[pharmacy_id] = next_number.get(pharmacy_id, 0) + 1
        prescription.rx_number = next_number[pharmacy_id]
        prescription.save(update_fields=["rx_number"])


def clear_rx_numbers(apps, schema_editor):
    apps.get_model("pharmacy", "Prescription").objects.update(rx_number=None)


class Migration(migrations.Migration):

    dependencies = [
        ("pharmacy", "0008_fills_rx_numbers_change_history"),
    ]

    operations = [
        migrations.RunPython(assign_rx_numbers, clear_rx_numbers),
        migrations.AlterField(
            model_name="prescription",
            name="rx_number",
            field=models.PositiveBigIntegerField(),
        ),
    ]

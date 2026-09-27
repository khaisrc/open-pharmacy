"""Request builders the prescription and fill tests share."""

import threading

from django.db import connection

PRESCRIPTIONS = "/api/v1/prescriptions/"
FILLS = "/api/v1/fills/"


def fills_url(prescription, fill=None, action=None):
    """The collection under its prescription, or one fill at its own
    top-level id (with an action)."""
    if fill is None:
        return f"{PRESCRIPTIONS}{prescription.pk}/fills/"
    url = f"{FILLS}{fill if isinstance(fill, str) else fill.pk}/"
    return f"{url}{action}/" if action else url


def prescription_body(patient, prescriber, medication, location=None, **overrides):
    body = {
        "patient_id": str(patient.pk),
        "prescriber_id": str(prescriber.pk),
        "medication_id": str(medication.pk),
        "intake_source": "provider",
        "dose_amount": "1 tablet",
        "quantity": 30,
    }
    if location is not None:
        body["pharmacy_location_id"] = str(location.pk)
    body.update(overrides)
    return body


def start_fill(client, prescription, **body):
    """POST a fill; `client` must already send a staff header."""
    return client.post(fills_url(prescription), body or None, format="json")


def dispense(client, prescription, **body):
    """The whole workflow -- create, verify, complete -- as a pharmacist.
    Returns the three responses; the caller asserts on whichever it cares
    about."""
    created = start_fill(client, prescription, **body)
    if created.status_code != 201:
        return created, None, None
    fill_id = created.data["id"]
    verified = client.post(fills_url(prescription, fill_id, "verify"))
    completed = client.post(fills_url(prescription, fill_id, "complete"))
    return created, verified, completed


def race(workers):
    """Run each callable in its own thread, started together, returning
    their results in order; a callable that raises returns its exception.
    Each thread closes its own connection so the test database can be torn
    down. Needs `django_db(transaction=True)`: a lock is invisible inside a
    single wrapping test transaction."""
    barrier = threading.Barrier(len(workers))
    results = [None] * len(workers)

    def run(index, work):
        try:
            barrier.wait()
            results[index] = work()
        except Exception as exc:
            results[index] = exc
        finally:
            connection.close()

    threads = [threading.Thread(target=run, args=(i, w)) for i, w in enumerate(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results

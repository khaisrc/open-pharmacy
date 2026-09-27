from django.db import connection

from pharmacy.models import Staff

from .base import Repository


class StaffRepository(Repository):
    model = Staff

    def all(self):
        return Staff.objects.select_related("pharmacy")

    def lock_subject(self, external_user_id):
        """Take a transaction-scoped advisory lock on one token subject, so
        concurrent first requests for it register one row."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))", [f"staff:{external_user_id}"]
            )

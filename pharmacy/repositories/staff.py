from pharmacy.models import Staff

from .base import Repository


class StaffRepository(Repository):
    model = Staff

    def all(self):
        return Staff.objects.select_related("pharmacy")

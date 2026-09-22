from pharmacy.models import Staff

from .base import Repository


class StaffRepository(Repository):
    model = Staff

    def live(self):
        return Staff.objects.select_related("pharmacy")

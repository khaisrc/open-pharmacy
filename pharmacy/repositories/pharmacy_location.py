from django.conf import settings

from pharmacy.models import PharmacyLocation

from .base import Repository


class PharmacyLocationRepository(Repository):
    model = PharmacyLocation

    def live(self):
        return PharmacyLocation.objects.select_related(
            "pharmacy", "address", "pharmacist_in_charge"
        )

    def home(self):
        """The store this deployment serves, or None if it is missing.

        Read from HOME_PHARMACY_LOCATION_NCPDP_ID rather than inferred from
        "whichever one exists", so a second store opening -- or a test creating
        its own location -- cannot change which site a prescription written
        without a location is routed to.
        """
        return self.find(ncpdp_id=settings.HOME_PHARMACY_LOCATION_NCPDP_ID).first()

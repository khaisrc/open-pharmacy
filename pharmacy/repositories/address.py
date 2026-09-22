from pharmacy.models import Address

from .base import Repository


class AddressRepository(Repository):
    model = Address

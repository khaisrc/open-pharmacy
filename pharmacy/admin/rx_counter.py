from django.contrib import admin

from pharmacy import repositories, services
from pharmacy.models import RxCounter

from .base import ServiceModelAdmin


@admin.register(RxCounter)
class RxCounterAdmin(ServiceModelAdmin):
    """Onboarding a pharmacy: create its counter with last_issued at or
    above any Rx number it already holds. Numbers are issued from it by the
    service under a lock, never edited here."""

    repository = repositories.rx_counters
    service = services.rx_counters
    list_display = ["pharmacy", "last_issued", "step"]
    autocomplete_fields = ["pharmacy"]

from django.contrib import admin

from pharmacy import repositories, services
from pharmacy.models import Patient

from .base import ServiceModelAdmin


@admin.register(Patient)
class PatientAdmin(ServiceModelAdmin):
    repository = repositories.patients
    service = services.patients
    list_display = ["last_name", "first_name", "dob", "phone_number", "email"]
    list_filter = ["gender", "sms_opt_in"]
    search_fields = ["first_name", "last_name", "email"]
    autocomplete_fields = ["address"]
    readonly_fields = ["id", "created_at", "updated_at"]

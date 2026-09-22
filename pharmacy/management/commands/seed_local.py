"""Idempotent local seeding: a superuser, and optional demo data.

Guarded in two independent places -- the entrypoint only calls this when
DJANGO_ENV is local, and the command re-checks. One guard in a shell script is
a typo away from planting a known-password superuser in a deployed environment.
"""

import os
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from pharmacy.models import (
    Address,
    LocationType,
    Medication,
    MedicationForm,
    Patient,
    Pharmacy,
    PharmacyLocation,
    PractitionerType,
    Prescriber,
    Prescription,
    PrescriptionOriginCode,
    PrescriptionStatus,
    RxCounter,
    Staff,
    StaffRole,
)

# Where every local pharmacy's Rx numbers start. High enough that a number
# never looks like a sequence position, and the same for every pharmacy so a
# local label is obviously local.
RX_COUNTER_START = 1_000_000_000


class Command(BaseCommand):
    help = "Seed a local superuser and, with SEED_DEMO_DATA=true, demo records."

    def handle(self, *args, **options):
        env = os.environ.get("DJANGO_ENV", "local")
        if env != "local":
            raise CommandError(f"seed_local refuses to run with DJANGO_ENV={env!r}.")

        self._seed_superuser()
        if os.environ.get("SEED_DEMO_DATA", "").lower() in {"1", "true", "yes"}:
            with transaction.atomic():
                self._seed_demo_data()
        else:
            self.stdout.write("SEED_DEMO_DATA is not set; skipping demo records.")

    def _seed_superuser(self):
        User = get_user_model()
        username = os.environ.get("LOCAL_SUPERUSER_USERNAME", "admin")
        password = os.environ.get("LOCAL_SUPERUSER_PASSWORD", "admin")
        email = os.environ.get("LOCAL_SUPERUSER_EMAIL", "admin@example.local")

        # get_or_create, not createsuperuser --noinput: that exits non-zero when
        # the user exists and would break every boot after the first.
        user, created = User.objects.get_or_create(
            username=username, defaults={"email": email, "is_staff": True, "is_superuser": True}
        )
        if created:
            user.set_password(password)
            user.save()
            self.stdout.write(self.style.SUCCESS(f"Created superuser {username!r}."))
        else:
            self.stdout.write(f"Superuser {username!r} already exists.")

    def _seed_demo_data(self):
        addresses = {
            key: Address.objects.get_or_create(
                line1=line1,
                postal_code=postal,
                defaults={"city": city, "state": state, "line2": line2},
            )[0]
            for key, (line1, line2, city, state, postal) in {
                "corner": ("18 Hillcrest Ave", "", "Ithaca", "NY", "14850"),
                "cvs_a": ("400 Elmira Rd", "", "Ithaca", "NY", "14850"),
                "cvs_b": ("744 S Meadow St", "Suite 2", "Ithaca", "NY", "14850"),
                "cvs_c": ("1104 Dryden Rd", "", "Ithaca", "NY", "14850"),
                "clinic": ("21 Medical Park Dr", "Suite 300", "Ithaca", "NY", "14850"),
                "patient_a": ("9 Cayuga St", "Apt 4B", "Ithaca", "NY", "14850"),
                "patient_b": ("332 Pine Tree Rd", "", "Ithaca", "NY", "14850"),
            }.items()
        }

        # Both shapes on purpose: an independent with one store, a chain with
        # several, so the nested route and location_count are exercised by a
        # plain boot rather than only by tests.
        corner, _ = Pharmacy.objects.get_or_create(
            name="Cayuga Corner Pharmacy",
            defaults={
                "legal_name": "Cayuga Corner Pharmacy LLC",
                "phone_number": "+16075550110",
                "email": "hello@cayugacorner.example",
            },
        )
        chain, _ = Pharmacy.objects.get_or_create(
            name="Northstar Drug",
            defaults={
                "legal_name": "Northstar Drug Stores, Inc.",
                "phone_number": "+18005550123",
                "website": "https://northstar.example",
            },
        )

        # A local pharmacy is onboarded as soon as it exists: without a
        # seeded counter, creating a prescription at any of its stores is a
        # 409 rx_counter_not_seeded. The home pharmacy (migration 0003's
        # Acme row) gets one too. get_or_create, so a re-run never rewinds
        # a counter that has since issued numbers.
        home = PharmacyLocation.objects.filter(
            ncpdp_id=settings.HOME_PHARMACY_LOCATION_NCPDP_ID
        ).first()
        for pharm in (corner, chain, *([home.pharmacy] if home else [])):
            RxCounter.objects.get_or_create(
                pharmacy=pharm, defaults={"last_issued": RX_COUNTER_START}
            )

        locations = {}
        for key, (pharm, store, ncpdp, npi, addr, ltype, hours) in {
            "corner": (corner, "", "1417231", "1487654323", "corner", LocationType.RETAIL, False),
            "north_a": (chain, "0412", "2938471", "1245319599", "cvs_a", LocationType.RETAIL, True),
            "north_b": (chain, "0587", "3847192", "", "cvs_b", LocationType.RETAIL, False),
            "north_c": (chain, "0913", "4756203", "", "cvs_c", LocationType.SPECIALTY, False),
        }.items():
            locations[key] = PharmacyLocation.objects.get_or_create(
                ncpdp_id=ncpdp,
                defaults={
                    "pharmacy": pharm,
                    "store_number": store,
                    "npi": npi,
                    "address": addresses[addr],
                    "location_type": ltype,
                    "is_24_hour": hours,
                    "phone_number": "+16075550" + ncpdp[:3],
                },
            )[0]

        # One pharmacist per pharmacy, designated PIC of its first store, plus
        # a technician of each kind, so every registration rule is visible
        # from a plain boot. Registered staff are keyed on the registration;
        # the unregistered technician on name and employer, the only identity
        # such a record has.
        staff = {}
        for key, (first, last, role, state, number, body, cert, npi, pharm) in {
            "ortiz": (
                "Elena",
                "Ortiz",
                StaffRole.PHARMACIST,
                "NY",
                "RPH.023417",
                "",
                "",
                "1356728844",
                corner,
            ),
            "walsh": (
                "Brendan",
                "Walsh",
                StaffRole.PHARMACIST,
                "NY",
                "RPH.031205",
                "",
                "",
                "1205849874",
                chain,
            ),
            "kim": (
                "Soo-jin",
                "Kim",
                StaffRole.CERTIFIED_TECHNICIAN,
                "NY",
                "RPT.118902",
                "PTCB",
                "10021987",
                "",
                chain,
            ),
            "diaz": ("Marco", "Diaz", StaffRole.TECHNICIAN, "", "", "", "", "", corner),
        }.items():
            lookup = (
                {"registration_state": state, "registration_number": number}
                if number
                else {"first_name": first, "last_name": last, "pharmacy": pharm}
            )
            staff[key] = Staff.objects.get_or_create(
                **lookup,
                defaults={
                    "first_name": first,
                    "last_name": last,
                    "role": role,
                    "registration_state": state,
                    "registration_number": number,
                    "certification_body": body,
                    "certification_number": cert,
                    "npi": npi,
                    "pharmacy": pharm,
                },
            )[0]

        for loc, pic in (("corner", "ortiz"), ("north_a", "walsh")):
            if locations[loc].pharmacist_in_charge_id is None:
                locations[loc].pharmacist_in_charge = staff[pic]
                locations[loc].save(update_fields=["pharmacist_in_charge", "updated_at"])

        medications = {}
        for key, (name, ndc, strength, form) in {
            "lisinopril": ("Lisinopril", "00093505601", "10 mg", MedicationForm.TABLET),
            "metformin": ("Metformin HCl", "00378018201", "500 mg", MedicationForm.TABLET),
            "amoxicillin": ("Amoxicillin", "00093416073", "500 mg", MedicationForm.CAPSULE),
            "albuterol": ("Albuterol Sulfate", "00173068220", "90 mcg", MedicationForm.OTHER),
        }.items():
            medications[key] = Medication.objects.get_or_create(
                ndc=ndc, defaults={"name": name, "strength": strength, "form": form}
            )[0]

        prescribers = {}
        for key, (first, last, npi, ptype) in {
            "chen": ("Alice", "Chen", "1234567893", PractitionerType.MD),
            "okafor": ("Daniel", "Okafor", "1861234569", PractitionerType.NP),
        }.items():
            prescribers[key] = Prescriber.objects.get_or_create(
                npi=npi,
                defaults={
                    "first_name": first,
                    "last_name": last,
                    "practitioner_type": ptype,
                    "address": addresses["clinic"],
                },
            )[0]

        patients = {}
        for key, (first, last, dob, gender, phone, addr) in {
            "rivera": ("Maria", "Rivera", "1981-04-12", "female", "+16075551201", "patient_a"),
            "novak": ("Peter", "Novak", "1967-11-30", "male", "+16075551202", "patient_b"),
        }.items():
            patients[key] = Patient.objects.get_or_create(
                first_name=first,
                last_name=last,
                dob=dob,
                defaults={
                    "gender": gender,
                    "phone_number": phone,
                    "email": f"{first.lower()}.{last.lower()}@example.local",
                    "address": addresses[addr],
                    "sms_opt_in": False,
                },
            )[0]

        now = timezone.now()
        # Each (patient, prescriber, medication) triple below is distinct, so it
        # is the identity a re-run looks up. The key must not involve time: a
        # lookup on written_at would match nothing after a day and re-insert
        # all four on every later boot.
        #
        # The Rx numbers are written directly: this command is fixture data
        # outside the service layer. They sit below RX_COUNTER_START so they
        # can never collide with a number the counter issues. No fills are
        # seeded, which is why the one terminal demo prescription is
        # cancelled rather than completed: completed would claim a dispensing
        # no fill row describes.
        demo_prescriptions = [
            (
                "rivera",
                "chen",
                "lisinopril",
                "north_a",
                1001,
                PrescriptionOriginCode.ELECTRONIC,
                "1 tablet",
                30,
                6,
                PrescriptionStatus.ACTIVE,
                "Take one tablet by mouth daily.",
            ),
            (
                "rivera",
                "okafor",
                "amoxicillin",
                "north_a",
                1002,
                PrescriptionOriginCode.WRITTEN,
                "1 capsule",
                21,
                1,
                PrescriptionStatus.ACTIVE,
                "One capsule three times daily for 7 days.",
            ),
            (
                "novak",
                "chen",
                "metformin",
                "corner",
                1,
                PrescriptionOriginCode.TELEPHONE,
                "2 tablets",
                60,
                3,
                PrescriptionStatus.CANCELLED,
                "Two tablets with the evening meal.",
            ),
            (
                "novak",
                "okafor",
                "albuterol",
                "north_c",
                1003,
                PrescriptionOriginCode.FACSIMILE,
                "2 puffs",
                1,
                2,
                PrescriptionStatus.ACTIVE,
                "Two puffs as needed.",
            ),
        ]
        for pat, presc, med, loc, rx, origin, dose, qty, auth, stat, sig in demo_prescriptions:
            Prescription.objects.get_or_create(
                patient=patients[pat],
                prescriber=prescribers[presc],
                medication=medications[med],
                defaults={
                    "pharmacy_location": locations[loc],
                    "rx_number": rx,
                    "prescription_origin_code": origin,
                    "dose_amount": dose,
                    "quantity": qty,
                    "fills_authorized": auth,
                    "status": stat,
                    "instructions": sig,
                    "written_at": now,
                    "expires_at": now + timedelta(days=365),
                },
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Demo data ready: {Pharmacy.objects.count()} pharmacies, "
                f"{PharmacyLocation.objects.count()} locations, "
                f"{Staff.objects.count()} staff, "
                f"{RxCounter.objects.count()} Rx counters, "
                f"{Prescription.objects.count()} prescriptions."
            )
        )

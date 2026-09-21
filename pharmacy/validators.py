"""Field-level validation shared across the domain.

The NPI check digit lives here once and is used by both Prescriber.npi and
PharmacyLocation.npi -- pharmacies carry organizational NPIs issued under the
same scheme, so there is one implementation rather than two.
"""

import re

from django.core.exceptions import ValidationError

E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")
POSTAL_RE = re.compile(r"^\d{5}(-\d{4})?$")

US_STATES = {
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
    "DC",
    "AS",
    "GU",
    "MP",
    "PR",
    "VI",
    "AA",
    "AE",
    "AP",
}


def validate_e164(value):
    if not E164_RE.match(value or ""):
        raise ValidationError(
            "Phone number must be E.164 formatted, for example +15551234567.",
            code="invalid_phone_number",
        )


def validate_postal_code(value):
    if not POSTAL_RE.match(value or ""):
        raise ValidationError(
            "Postal code must be 12345 or 12345-6789.", code="invalid_postal_code"
        )


def validate_us_state(value):
    if (value or "").upper() not in US_STATES:
        raise ValidationError(
            "%(value)s is not a USPS state code.", params={"value": value}, code="invalid_state"
        )


def luhn_check_digit(digits):
    """Luhn check digit for a string of digits, as used by the NPI scheme."""
    total = 0
    for index, char in enumerate(reversed(digits)):
        digit = int(char)
        if index % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return (10 - (total % 10)) % 10


def validate_npi(value):
    """An NPI is 10 digits whose last is a Luhn check digit over 80840 + prefix.

    Length alone is not enough: a typo'd NPI is otherwise indistinguishable from
    a real one and propagates into claims.
    """
    value = value or ""
    if not re.fullmatch(r"\d{10}", value):
        raise ValidationError("NPI must be exactly 10 digits.", code="invalid_npi")
    if luhn_check_digit("80840" + value[:9]) != int(value[9]):
        raise ValidationError("NPI check digit is invalid.", code="invalid_npi_check_digit")


def normalize_ndc(value):
    """Collapse an NDC to its 11-digit form.

    Real NDCs arrive as 4-4-2, 5-3-2, 5-4-1 and 5-4-2 segments that all denote the
    same product; normalizing on save is what makes `unique` actually mean unique.
    The first three are the 10-digit layouts, each padding a different segment;
    5-4-2 is already 11 digits and passes through unchanged.
    """
    raw = (value or "").strip()
    if not raw:
        raise ValidationError("NDC is required.", code="invalid_ndc")

    if "-" in raw:
        segments = raw.split("-")
        if len(segments) != 3 or not all(s.isdigit() for s in segments):
            raise ValidationError(
                "NDC must be three digit segments, for example 0002-8215-01.",
                code="invalid_ndc",
            )
        labeler, product, package = segments
        if (len(labeler), len(product), len(package)) not in {
            (4, 4, 2),
            (5, 3, 2),
            (5, 4, 1),
            (5, 4, 2),
        }:
            raise ValidationError(
                "NDC segments must be 4-4-2, 5-3-2, 5-4-1 or 5-4-2.", code="invalid_ndc"
            )
        return f"{labeler.zfill(5)}{product.zfill(4)}{package.zfill(2)}"

    if not raw.isdigit():
        raise ValidationError("NDC must contain only digits and dashes.", code="invalid_ndc")
    if len(raw) == 11:
        return raw
    raise ValidationError(
        "An unsegmented NDC must be 11 digits; segment it to disambiguate a 10-digit code.",
        code="invalid_ndc",
    )


def normalize_ncpdp(value):
    """NCPDP/NABP provider ID: 7 digits, separators stripped.

    Format only. The NCPDP check-digit scheme is deliberately not implemented --
    validating length and charset honestly beats enforcing an algorithm that has
    not been verified against the specification and would reject valid IDs.
    """
    raw = re.sub(r"[\s-]", "", (value or "").strip())
    if not re.fullmatch(r"\d{7}", raw):
        raise ValidationError("NCPDP ID must be 7 digits.", code="invalid_ncpdp")
    return raw


def validate_ncpdp(value):
    normalize_ncpdp(value)

import pytest
from django.core.exceptions import ValidationError

from pharmacy.validators import (
    normalize_ncpdp,
    normalize_ndc,
    validate_e164,
    validate_npi,
    validate_postal_code,
    validate_us_state,
)


class TestNPI:
    # 1234567893 is the canonical valid example from the NPI check-digit spec.
    def test_accepts_valid_check_digit(self):
        validate_npi("1234567893")

    def test_rejects_bad_check_digit(self):
        with pytest.raises(ValidationError) as exc:
            validate_npi("1234567890")
        assert exc.value.code == "invalid_npi_check_digit"

    @pytest.mark.parametrize("value", ["123456789", "12345678901", "abcdefghij", ""])
    def test_rejects_malformed(self, value):
        with pytest.raises(ValidationError):
            validate_npi(value)


class TestNDC:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("0002-8215-01", "00002821501"),  # 4-4-2
            ("00093-505-01", "00093050501"),  # 5-3-2
            ("00173-0682-2", "00173068202"),  # 5-4-1
            ("24299-4017-76", "24299401776"),  # 5-4-2, already 11 digits
            ("00093505601", "00093505601"),  # already normalized
        ],
    )
    def test_normalizes_every_segment_format(self, raw, expected):
        assert normalize_ndc(raw) == expected
        assert len(normalize_ndc(raw)) == 11

    @pytest.mark.parametrize("value", ["", "abc", "1234567890", "1-2", "0002-8215-011"])
    def test_rejects_malformed(self, value):
        with pytest.raises(ValidationError):
            normalize_ndc(value)


class TestNCPDP:
    @pytest.mark.parametrize("raw", ["1417231", "141-7231", " 1417231 "])
    def test_strips_separators(self, raw):
        assert normalize_ncpdp(raw) == "1417231"

    @pytest.mark.parametrize("value", ["123456", "12345678", "abcdefg", ""])
    def test_rejects_wrong_length(self, value):
        with pytest.raises(ValidationError):
            normalize_ncpdp(value)


class TestOtherFormats:
    @pytest.mark.parametrize("value", ["+15551234567", "+442071234567"])
    def test_accepts_e164(self, value):
        validate_e164(value)

    @pytest.mark.parametrize("value", ["5551234567", "+0555123", "555-123-4567", ""])
    def test_rejects_non_e164(self, value):
        with pytest.raises(ValidationError):
            validate_e164(value)

    @pytest.mark.parametrize("value", ["14850", "14850-1234"])
    def test_accepts_postal_code(self, value):
        validate_postal_code(value)

    @pytest.mark.parametrize("value", ["1485", "148501", "ABCDE", "14850-12"])
    def test_rejects_postal_code(self, value):
        with pytest.raises(ValidationError):
            validate_postal_code(value)

    def test_state_accepts_usps_code_any_case(self):
        validate_us_state("ny")

    def test_state_rejects_unknown(self):
        with pytest.raises(ValidationError):
            validate_us_state("ZZ")

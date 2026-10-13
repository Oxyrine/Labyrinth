"""GSTIN 15-character format and modulo-36 checksum validators."""

from __future__ import annotations

import re

from contract.models import GSTIN_ALPHABET, compute_gstin_check_char, verify_gstin_checksum

VALID_STATE_CODES: frozenset[str] = frozenset({f"{i:02d}" for i in range(1, 39)} | {"97"})
PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
ENTITY_RE = re.compile(r"^[1-9A-Z]$")


def validate_gstin_format(gstin: str) -> tuple[bool, str]:
    """Validate 15-char GSTIN structure: state code (01..38 or 97) + PAN (10) + entity + 'Z' + check char."""
    if not isinstance(gstin, str):
        return False, "GSTIN must be a string."
    s = gstin.strip().upper()
    if len(s) != 15:
        return False, f"GSTIN {s!r} has length {len(s)}; expected 15 characters."
    state_code = s[:2]
    if state_code not in VALID_STATE_CODES:
        return False, f"GSTIN {s} has invalid Indian state code {state_code!r} (expected 01..38 or 97)."
    pan = s[2:12]
    if not PAN_RE.match(pan):
        return False, f"GSTIN {s} has invalid embedded PAN {pan!r} (expected 5 letters, 4 digits, 1 letter)."
    entity_code = s[12]
    if not ENTITY_RE.match(entity_code):
        return False, f"GSTIN {s} has invalid 13th entity code {entity_code!r} (expected 1-9 or A-Z)."
    if s[13] != "Z":
        return False, f"GSTIN {s} has 14th character {s[13]!r}; expected 'Z'."
    if s[14] not in GSTIN_ALPHABET:
        return False, f"GSTIN {s} has invalid 15th check character {s[14]!r}."
    return True, f"GSTIN {s} has valid 15-character format (state {state_code}, PAN {pan})."


def validate_gstin_checksum(gstin: str) -> tuple[bool, str]:
    """Validate the 15th character modulo-36 check digit of a GSTIN."""
    if not isinstance(gstin, str):
        return False, "GSTIN must be a string."
    s = gstin.strip().upper()
    if len(s) != 15 or any(ch not in GSTIN_ALPHABET for ch in s):
        return (
            False,
            f"GSTIN {s!r} cannot be verified for mod-36 checksum because it is not 15 alphanumeric characters.",
        )
    expected = compute_gstin_check_char(s[:14])
    if not verify_gstin_checksum(s):
        return (
            False,
            f"GSTIN {s} failed mod-36 checksum verification (expected check char '{expected}', found '{s[14]}').",
        )
    return True, f"GSTIN {s} passed mod-36 checksum verification (check char '{expected}')."

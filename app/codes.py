"""Barcode normalisation.

A UPC-A (12 digits) and the same product's EAN-13 (a leading 0 added) are the
same code. Different scanners and different firmware settings emit either, so
we canonicalise: digit-only codes longer than 12 characters lose leading zeros
until they are 12 long (or no longer start with 0). Everything else (our own
QM000123 labels, odd symbologies) is only trimmed and upper-cased.
"""
import re

MAX_LEN = 64
_PRINTABLE = re.compile(r"^[\x21-\x7e]+$")  # visible ASCII, no spaces


def normalize_code(raw: str) -> str:
    code = (raw or "").strip()
    if not code or len(code) > MAX_LEN or not _PRINTABLE.match(code):
        raise ValueError("invalid code")
    if code.isdigit():
        while len(code) > 12 and code[0] == "0":
            code = code[1:]
        return code
    return code.upper()

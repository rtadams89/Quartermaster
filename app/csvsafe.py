"""Spreadsheet-formula protection for CSV files.

Excel and similar programs run a cell as a formula when it starts with = + - @ (or a tab or carriage
return). Names, notes and codes come from users and from online listings, so text cells that start with one
of those characters are written with a leading apostrophe, which spreadsheets show as plain text. The product
import removes that apostrophe again, so an export can be imported back unchanged."""

_RISKY = ("=", "+", "-", "@", "\t", "\r")


def safe(cell):
    """A CSV cell that cannot run as a formula. Numbers are left alone."""
    if isinstance(cell, str) and cell.startswith(_RISKY):
        return "'" + cell
    return cell


def safe_row(row):
    return [safe(c) for c in row]


def unsafe(text: str) -> str:
    """Undo `safe` on a cell read back from a file."""
    return text[1:] if text.startswith("'") and text[1:].startswith(_RISKY) else text

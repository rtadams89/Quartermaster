"""Spreadsheet-formula protection for CSV files.

Text cells that start with = + - @ (or a tab or carriage return) are written with a leading apostrophe,
which spreadsheets show as plain text. The product import removes that apostrophe again."""

_RISKY = ("=", "+", "-", "@", "\t", "\r")


def safe(cell):
    """A CSV cell with a leading apostrophe added when it starts with a formula character. Numbers are unchanged."""
    if isinstance(cell, str) and cell.startswith(_RISKY):
        return "'" + cell
    return cell


def safe_row(row):
    return [safe(c) for c in row]


def unsafe(text: str) -> str:
    """Undo `safe` on a cell read back from a file."""
    return text[1:] if text.startswith("'") and text[1:].startswith(_RISKY) else text

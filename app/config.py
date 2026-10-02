"""Runtime configuration, read from environment variables (see .env.example)."""
import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


# Where the SQLite file lives. In Docker this is the mounted /data volume.
DB_PATH = os.environ.get("QM_DB_PATH", "./data/quartermaster.db")

# Sessions (kiosk and admin) lock after this many minutes with no activity.
IDLE_MINUTES = _int("QM_IDLE_MINUTES", 15)

# Per-source-IP PIN lockout. After LOCKOUT_THRESHOLD consecutive failures from an
# IP, that IP is locked out for LOCKOUT_BASE_SECONDS, doubling with every further
# failure, capped at LOCKOUT_MAX_SECONDS. Other IPs are unaffected.
LOCKOUT_THRESHOLD = _int("QM_LOCKOUT_THRESHOLD", 5)
LOCKOUT_BASE_SECONDS = _int("QM_LOCKOUT_BASE_SECONDS", 60)
LOCKOUT_MAX_SECONDS = _int("QM_LOCKOUT_MAX_SECONDS", 3600)
# Failure counters for an IP are forgotten after this many hours of quiet.
LOCKOUT_FORGET_HOURS = _int("QM_LOCKOUT_FORGET_HOURS", 24)

# Set to true ONLY when running behind a reverse proxy you control that appends
# the real client address to X-Forwarded-For. Otherwise the header is ignored
# (it is trivially spoofable and would let someone dodge the lockout).
TRUST_PROXY = _bool("QM_TRUST_PROXY", False)

# Send the session cookie with the Secure flag (set true if you serve HTTPS).
COOKIE_SECURE = _bool("QM_COOKIE_SECURE", False)

# Look up unidentified barcodes on UPCitemdb (free, no key) to pre-fill the product form.
# Only the barcode number is sent. Set to false to keep the server fully offline.
UPC_LOOKUP = _bool("QM_UPC_LOOKUP", True)

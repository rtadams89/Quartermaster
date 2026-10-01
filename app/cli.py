"""Recovery tools. Run inside the container:

    docker compose exec quartermaster python -m app.cli reset-pin 1234
    docker compose exec quartermaster python -m app.cli clear-lockouts
"""
import re
import sys

from sqlalchemy import delete

from .db import Base, SessionLocal, engine
from .models import LoginAttempt
from .security import clear_sessions, set_pin


def main(argv: list[str]) -> int:
    Base.metadata.create_all(engine)
    if len(argv) >= 2 and argv[0] == "reset-pin":
        if not re.fullmatch(r"\d{4}", argv[1]):
            print("PIN must be exactly 4 digits")
            return 2
        with SessionLocal() as db:
            set_pin(db, argv[1])
            clear_sessions(db)
        print("PIN changed; all sessions signed out.")
        return 0
    if argv[:1] == ["clear-lockouts"]:
        with SessionLocal() as db:
            db.execute(delete(LoginAttempt))
            db.commit()
        print("All lockouts cleared.")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Bootstraps the first administrator account (Step 46).

There is no public registration endpoint anywhere in this application
(deliberately — see backend/README.md's "Authentication and
Authorization" section) and no account exists at all in a fresh
database, so without this script nothing could ever log in to create
the first user through `POST /api/v1/admin/users` (which itself
requires an existing administrator). This script is the one
intentional exception: it is run directly on the server, never over
HTTP, and only ever by whoever already has shell/database access to
the deployment — the same trust boundary as being able to read
`backend/data/ai_ids.db` directly.

Run from the backend/ directory with its virtual environment active:

    .venv/Scripts/python.exe scripts/create_admin_user.py --username admin   (Windows)
    .venv/bin/python scripts/create_admin_user.py --username admin           (macOS/Linux)

Prompts for a password interactively (never accepted as a plain
command-line argument, which would leak it into shell history/process
listings). Refuses to create a second account with the same username,
and refuses an empty password — the same validation `app.services.
user_management_service.create_user` already enforces, called here
directly rather than duplicated.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.errors import ApiException  # noqa: E402
from app.db.database import SessionLocal, init_db  # noqa: E402
from app.models.enums import UserRole  # noqa: E402
from app.services.user_management_service import create_user  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True, help="The new administrator's username.")
    args = parser.parse_args()

    password = getpass.getpass("New administrator password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords do not match.", file=sys.stderr)
        raise SystemExit(1)

    init_db()
    db = SessionLocal()
    try:
        user = create_user(db, username=args.username, password=password, role=UserRole.ADMINISTRATOR)
    except ApiException as error:
        print(f"Could not create the account: {error.message}", file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        db.close()

    print(f"Created administrator '{user.username}' ({user.id}).")


if __name__ == "__main__":
    main()

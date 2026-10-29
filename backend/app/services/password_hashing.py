"""Password hashing (Step 46): PBKDF2-HMAC-SHA256 with a fresh random
salt per password — a recognized, NIST-approved (SP 800-132) password-
hashing construction, implemented entirely with the Python standard
library (`hashlib`, `secrets`) so this step adds no new dependency for
it.

Stored format: `pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>` — the
iteration count travels with the hash, so a future increase to
`DEFAULT_ITERATIONS` never invalidates already-stored hashes (`verify_
password` always uses the iteration count recorded *in* the hash being
checked, never the current default).

Nothing here ever logs, returns, or stores a plaintext password —
`hash_password`'s input is used only long enough to compute the digest.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

ALGORITHM = "pbkdf2_sha256"
DEFAULT_ITERATIONS = 310_000
"""In line with OWASP's current PBKDF2-SHA256 guidance (approximately
300k+ iterations) at the time this step was written."""
_SALT_BYTES = 16
_HASH_NAME = "sha256"


def hash_password(password: str, *, iterations: int = DEFAULT_ITERATIONS) -> str:
    """Returns a self-describing hash string safe to store in
    `User.password_hash`. Raises `ValueError` for an empty password —
    never hashes and stores an empty credential."""
    if not password:
        raise ValueError("password must not be empty")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(_HASH_NAME, password.encode("utf-8"), salt, iterations)
    return f"{ALGORITHM}${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Constant-time comparison against a hash `hash_password` produced.
    Returns `False` (never raises) for a malformed/corrupt stored hash
    or an unrecognized algorithm tag — a verification failure, not a
    crash, is the only safe response to unexpected stored data.
    """
    try:
        algorithm, iterations_text, salt_hex, digest_hex = stored_hash.split("$")
        if algorithm != ALGORITHM:
            return False
        iterations = int(iterations_text)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, AttributeError):
        return False

    actual = hashlib.pbkdf2_hmac(_HASH_NAME, password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


__all__ = ["ALGORITHM", "DEFAULT_ITERATIONS", "hash_password", "verify_password"]

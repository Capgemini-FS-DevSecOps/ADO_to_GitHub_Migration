"""Password hashing utilities (PBKDF2, no extra dependencies)."""
from __future__ import annotations

import hashlib
import hmac
import secrets


def hash_password(password: str) -> str:
    """Hash a password with a freshly generated random salt.

    Args:
        password: The plaintext password to hash.

    Returns:
        A ``$``-separated string holding the algorithm label, the hex salt and
        the hex PBKDF2-HMAC-SHA256 digest, suitable for storing in the
        ``password_hash`` column and for passing back to ``verify_password``.
    """
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000,
    )
    return f"pbkdf2-sha256${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Check a plaintext password against a stored hash.

    The digests are compared with ``hmac.compare_digest`` so the check runs in
    constant time. A malformed or unrecognised stored value is treated as a
    failed match rather than an error.

    Args:
        password: The plaintext password supplied at login.
        stored: A hash previously produced by ``hash_password``.

    Returns:
        True when the password matches the stored hash, otherwise False.
    """
    try:
        algo, salt, digest_hex = stored.split("$", 2)
        if algo != "pbkdf2-sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000,
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, AttributeError):
        return False


def validate_password_strength(password: str) -> None:
    """Check a password against the minimum platform policy.

    Args:
        password: The plaintext password to check.

    Raises:
        ValueError: The password is shorter than the minimum length.
    """
    if len(password) < 12:
        raise ValueError("Password must be at least 12 characters")

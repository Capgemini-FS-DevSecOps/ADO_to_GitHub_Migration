"""Password hashing utilities (PBKDF2, no extra dependencies)."""
from __future__ import annotations

import hashlib
import hmac
import secrets


def hash_password(password: str) -> str:
    """Hash password with random salt."""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000,
    )
    return f"pbkdf2-sha256${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Verify password against stored hash."""
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
    """Raise ValueError if password does not meet minimum policy."""
    if len(password) < 12:
        raise ValueError("Password must be at least 12 characters")

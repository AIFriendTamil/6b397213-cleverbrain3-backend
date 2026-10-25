"""Password hashing and verification utilities using Argon2 and bcrypt."""

import logging
import bcrypt
from passlib.context import CryptContext
from passlib.exc import UnknownHashError

# Patch bcrypt compatibility for passlib 1.7.4 on bcrypt >= 4.0
if not hasattr(bcrypt, "__about__"):

    class _BcryptAbout:
        __version__ = getattr(bcrypt, "__version__", "4.2.0")

    bcrypt.__about__ = _BcryptAbout()  # type: ignore[attr-defined]

logger = logging.getLogger(__name__)

# Configure Passlib CryptContext with Argon2id as primary scheme and bcrypt as fallback
pwd_context = CryptContext(
    schemes=["argon2", "bcrypt"],
    deprecated="auto",
)


def hash_password(password: str) -> str:
    """Hash a plaintext password using Argon2.

    Args:
        password: Plaintext password string to hash.

    Returns:
        Hashed password string.

    Raises:
        ValueError: If password is not a non-empty string.
    """
    if not isinstance(password, str) or not password:
        raise ValueError("Password must be a non-empty string")

    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a stored hash (Argon2 or bcrypt).

    Args:
        plain_password: Plaintext password to verify.
        hashed_password: Stored password hash string.

    Returns:
        True if the plaintext password matches the hash, False otherwise.
    """
    if not isinstance(plain_password, str) or not isinstance(hashed_password, str):
        return False
    if not plain_password or not hashed_password:
        return False

    try:
        return pwd_context.verify(plain_password, hashed_password)
    except (UnknownHashError, ValueError, TypeError) as exc:
        logger.warning("Failed to verify password hash format: %s", exc)
        return False
    except Exception as exc:
        logger.error("Unexpected exception during password verification: %s", exc)
        return False


def needs_rehash(hashed_password: str) -> bool:
    """Check if a stored password hash needs rehashing to the primary scheme (Argon2).

    Args:
        hashed_password: Stored password hash string.

    Returns:
        True if the hash should be updated, False otherwise.
    """
    if not isinstance(hashed_password, str) or not hashed_password:
        return False

    try:
        return pwd_context.needs_update(hashed_password)
    except Exception as exc:
        logger.warning("Failed to check if hash needs rehash: %s", exc)
        return False

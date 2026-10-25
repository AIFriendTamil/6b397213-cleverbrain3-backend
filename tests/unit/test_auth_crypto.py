"""Unit tests for password crypto service (Argon2 / bcrypt hashing and verification)."""

import bcrypt
import pytest

from src.services.auth.crypto import hash_password, needs_rehash, verify_password


def test_hash_password_success():
    """Verify hash_password returns an Argon2 hash string."""
    password = "SuperSecretPassword123!"
    hashed = hash_password(password)

    assert isinstance(hashed, str)
    assert len(hashed) > 0
    assert "$argon2" in hashed
    assert hashed != password


def test_hash_password_salt_uniqueness():
    """Verify hashing the same password twice produces different hashes due to salting."""
    password = "MySecurePassword"
    hash1 = hash_password(password)
    hash2 = hash_password(password)

    assert hash1 != hash2


def test_hash_password_invalid_input():
    """Verify hash_password raises ValueError for invalid inputs."""
    with pytest.raises(ValueError, match="Password must be a non-empty string"):
        hash_password("")

    with pytest.raises(ValueError, match="Password must be a non-empty string"):
        hash_password(None)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Password must be a non-empty string"):
        hash_password(12345)  # type: ignore[arg-type]


def test_verify_password_argon2():
    """Verify verify_password correctly validates matching and non-matching passwords against Argon2 hash."""
    password = "CorrectPassword123"
    hashed = hash_password(password)

    assert verify_password(password, hashed) is True
    assert verify_password("WrongPassword123", hashed) is False
    assert verify_password("correctpassword123", hashed) is False  # Case sensitivity


def test_verify_password_bcrypt():
    """Verify verify_password correctly validates matching and non-matching passwords against bcrypt hash."""
    password = "BcryptPassword456"
    bcrypt_salt = bcrypt.gensalt()
    bcrypt_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt_salt).decode("utf-8")

    assert "$2b$" in bcrypt_hash or "$2a$" in bcrypt_hash
    assert verify_password(password, bcrypt_hash) is True
    assert verify_password("WrongBcryptPassword", bcrypt_hash) is False


def test_verify_password_invalid_inputs():
    """Verify verify_password safely handles empty strings, None, or non-string inputs by returning False."""
    valid_hash = hash_password("ValidPassword123")

    assert verify_password("", valid_hash) is False
    assert verify_password("ValidPassword123", "") is False
    assert verify_password("", "") is False
    assert verify_password(None, valid_hash) is False  # type: ignore[arg-type]
    assert verify_password("ValidPassword123", None) is False  # type: ignore[arg-type]


def test_verify_password_corrupted_hash():
    """Verify verify_password safely handles malformed hash strings without raising uncaught exceptions."""
    assert verify_password("SomePassword", "not_a_valid_hash_format") is False
    assert verify_password("SomePassword", "$invalid$scheme$hash") is False


def test_needs_rehash():
    """Verify needs_rehash identifies bcrypt hashes as needing rehash and fresh Argon2 as not needing rehash."""
    password = "RehashCheckPassword"
    argon2_hash = hash_password(password)

    # Fresh Argon2 hash should not need rehash
    assert needs_rehash(argon2_hash) is False

    # Bcrypt hash should indicate rehash is required (deprecated scheme relative to Argon2 primary)
    bcrypt_salt = bcrypt.gensalt()
    bcrypt_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt_salt).decode("utf-8")
    assert needs_rehash(bcrypt_hash) is True

    # Invalid input should return False safely
    assert needs_rehash("") is False
    assert needs_rehash(None) is False  # type: ignore[arg-type]

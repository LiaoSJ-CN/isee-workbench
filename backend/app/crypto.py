"""Transparent encryption/decryption for sensitive stored fields.

Uses Fernet (AES-128-CBC with HMAC-SHA256) for authenticated symmetric
encryption. The encryption key is sourced from ``ENCRYPTION_KEY`` env var
(see ``config.py``).  Existing plaintext values in the database are
detected at read time and returned unchanged so the system continues to
work after the encryption feature is first enabled — they will be
re-encrypted on the next update.
"""

import logging

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

logger = logging.getLogger(__name__)

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    """Lazy-init the Fernet instance so config is resolved first."""
    global _fernet
    if _fernet is None:
        _fernet = Fernet(settings.encryption_key.encode())
    return _fernet


def encrypt(stored: str) -> str:
    """Encrypt a plaintext value and return the base64-encoded token.

    The returned string can be stored directly into the database column
    that previously held the plaintext value.
    """
    return _get_fernet().encrypt(stored.encode()).decode()


def decrypt(stored: str) -> str:
    """Decrypt a value previously produced by :func:`encrypt`.

    If *stored* is a legacy plaintext value (does not look like a Fernet
    token), it is returned as-is so existing data sources keep working
    after the encryption feature is first enabled.  Those passwords will
    be re-encrypted on the next update of the data source.

    When *stored* looks like a Fernet token but fails decryption — most
    likely because ``ENCRYPTION_KEY`` was changed — a warning is logged
    so operators can distinguish a genuine key-mismatch from legacy
    plaintext.
    """
    try:
        return _get_fernet().decrypt(stored.encode()).decode()
    except InvalidToken:
        if stored.startswith("gAAAAA"):
            # Looks like a Fernet token but can't be decrypted — almost
            # certainly a key mismatch. Returning the raw ciphertext would
            # cause confusing downstream auth errors, so fail loudly.
            logger.error(
                "Failed to decrypt a stored value that looks like a Fernet "
                "token. This usually means ENCRYPTION_KEY was changed after "
                "data-source passwords were encrypted. Restore the original "
                "ENCRYPTION_KEY or re-save each data-source password."
            )
            raise ValueError(
                "ENCRYPTION_KEY mismatch — stored passwords cannot be decrypted. "
                "Restore the original key or re-save data-source passwords."
            ) from None
        # Legacy plaintext — return as-is so existing data sources keep
        # working after encryption is first enabled.
        return stored

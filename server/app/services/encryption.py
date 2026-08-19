"""
Simple AES-256-GCM encryption utilities for project secrets.

Usage:
    from app.services.encryption import encrypt_secret, decrypt_secret

    encrypted = encrypt_secret("my-api-key", encryption_key)
    decrypted = decrypt_secret(encrypted, encryption_key)
"""

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def get_encryption_key() -> bytes:
    """Get the encryption key from environment, deriving a 32-byte key."""
    key_str = os.environ.get("ENCRYPTION_KEY")
    if not key_str:
        raise ValueError("ENCRYPTION_KEY environment variable not set")

    # If key is base64 encoded (recommended), decode it
    try:
        key_bytes = base64.b64decode(key_str)
        if len(key_bytes) == 32:
            return key_bytes
    except Exception:
        pass

    # Otherwise, derive 32 bytes from the string using SHA-256
    import hashlib

    return hashlib.sha256(key_str.encode()).digest()


def encrypt_secret(plaintext: str, key: bytes | None = None) -> bytes:
    """
    Encrypt a secret using AES-256-GCM.

    Returns: nonce (12 bytes) + ciphertext + tag (16 bytes)
    """
    if key is None:
        key = get_encryption_key()

    aesgcm = AESGCM(key)
    nonce = os.urandom(12)  # 96-bit nonce for GCM
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)

    # Return nonce + ciphertext (includes auth tag)
    return nonce + ciphertext


def decrypt_secret(encrypted: bytes, key: bytes | None = None) -> str:
    """
    Decrypt a secret encrypted with encrypt_secret.

    Expects: nonce (12 bytes) + ciphertext + tag
    """
    if key is None:
        key = get_encryption_key()

    nonce = encrypted[:12]
    ciphertext = encrypted[12:]

    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)

    return plaintext.decode("utf-8")

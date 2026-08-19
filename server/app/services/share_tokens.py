"""Utilities for hashing and verifying public share visitor tokens."""

from __future__ import annotations

import hashlib
import os
from uuid import UUID

_DEFAULT_ITERATIONS = 150_000


def _iterations() -> int:
    try:
        return int(os.getenv("SHARE_TOKEN_PBKDF_ITERATIONS", str(_DEFAULT_ITERATIONS)))
    except ValueError:
        return _DEFAULT_ITERATIONS


def hash_share_token(token: str, share_id: UUID) -> bytes:
    """Derive a deterministic hash for a share visitor token.

    A per-share salt (the share UUID bytes) keeps hashes distinct while
    allowing us to perform equality lookups for a returning visitor.
    """

    if not token:
        raise ValueError("Token must not be empty")
    return hashlib.pbkdf2_hmac(
        "sha256",
        token.encode("utf-8"),
        share_id.bytes,
        _iterations(),
    )


def verify_share_token(token: str, share_id: UUID, expected_hash: bytes | None) -> bool:
    """Constant-time equality check for a visitor token hash."""

    if expected_hash is None:
        return False
    candidate = hash_share_token(token, share_id)
    if len(candidate) != len(expected_hash):
        return False
    result = 0
    for a, b in zip(candidate, expected_hash, strict=False):
        result |= a ^ b
    return result == 0

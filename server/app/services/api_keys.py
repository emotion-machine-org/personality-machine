from __future__ import annotations

import base64
import hashlib
import os
import secrets
from dataclasses import dataclass
from typing import Tuple

API_KEY_PREFIX_ROOT = "emk"


@dataclass(frozen=True)
class ParsedApiKey:
    prefix: str
    secret: str


def _stage_prefix() -> str:
    stage = os.getenv("API_KEY_ENV", os.getenv("ENV", "dev") or "dev")
    stage = "".join(ch for ch in stage.lower() if ch.isalnum() or ch in ("-",))
    if not stage:
        stage = "dev"
    random_tag = secrets.token_urlsafe(6)
    sanitized = "".join(ch for ch in random_tag if ch.isalnum()).lower()
    return f"{API_KEY_PREFIX_ROOT}_{stage}_{sanitized[:12]}"


def generate_project_api_key() -> Tuple[str, str, bytes, bytes]:
    """Return (full_key, prefix, salt, hash)."""
    prefix = _stage_prefix()
    secret_bytes = secrets.token_bytes(32)
    secret_token = base64.urlsafe_b64encode(secret_bytes).decode("ascii").rstrip("=")
    salt = secrets.token_bytes(16)
    secret_hash = hash_secret(secret_token, salt)
    full_key = f"{prefix}.{secret_token}"
    return full_key, prefix, salt, secret_hash


def hash_secret(secret: str, salt: bytes, iterations: int = 200_000) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, iterations, dklen=32)


def verify_secret(secret: str, salt: bytes, expected_hash: bytes) -> bool:
    attempt = hash_secret(secret, salt)
    return secrets.compare_digest(attempt, expected_hash)


def parse_api_key(raw_token: str) -> ParsedApiKey:
    if not raw_token or "." not in raw_token:
        raise ValueError("Invalid API key format")
    prefix, secret = raw_token.split(".", 1)
    if not prefix.startswith(f"{API_KEY_PREFIX_ROOT}_") or not secret:
        raise ValueError("Invalid API key format")
    return ParsedApiKey(prefix=prefix, secret=secret)

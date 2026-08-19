from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv
from fastapi import HTTPException, UploadFile, status

load_dotenv()

logger = logging.getLogger(__name__)

_MAX_UPLOAD_MB = float(os.getenv("KNOWLEDGE_UPLOAD_MAX_MB", "5"))
_MAX_UPLOAD_BYTES = int(_MAX_UPLOAD_MB * 1024 * 1024)

_ALLOWED_EXTENSIONS = {".txt", ".md", ".markdown", ".json", ".jsonl", ".ndjson"}
_ALLOWED_MIME_TYPES = {
    "text/plain",
    "text/markdown",
    "text/x-markdown",
    "application/json",
    "application/jsonl",
    "application/x-ndjson",
}

_S3_BUCKET = os.getenv("KNOWLEDGE_S3_BUCKET")
_S3_PREFIX = os.getenv("KNOWLEDGE_S3_PREFIX", "").strip("/")
_S3_REGION = os.getenv("KNOWLEDGE_S3_REGION") or os.getenv("AWS_REGION")
_S3_CLIENT = None


@dataclass
class StoredAsset:
    storage_key: str
    size_bytes: int
    checksum: str
    filename: str
    mime_type: str


def _sanitize_filename(filename: str | None) -> str:
    if not filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Filename is required")
    sanitized = Path(filename).name
    if not sanitized:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid filename")
    return sanitized


def _validate_file_type(filename: str, mime_type: str | None) -> None:
    ext = Path(filename).suffix.lower()
    content_type = (mime_type or "").lower() or "application/octet-stream"
    if ext not in _ALLOWED_EXTENSIONS and content_type not in _ALLOWED_MIME_TYPES:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported file type. Allowed: txt, md, json/jsonl",
        )


def _require_bucket() -> str:
    if not _S3_BUCKET:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, detail="KNOWLEDGE_S3_BUCKET is not configured"
        )
    return _S3_BUCKET


def _get_s3_client():
    global _S3_CLIENT
    if _S3_CLIENT is None:
        client_kwargs = {}
        if _S3_REGION:
            client_kwargs["region_name"] = _S3_REGION
        _S3_CLIENT = boto3.client("s3", **client_kwargs)
    return _S3_CLIENT


def _build_storage_key(project_id: UUID, asset_id: UUID, filename: str) -> str:
    safe_name = _sanitize_filename(filename)
    key_parts = [str(project_id), str(asset_id), safe_name]
    key = "/".join(key_parts)
    if _S3_PREFIX:
        return f"{_S3_PREFIX}/{key}"
    return key


def delete_asset(storage_key: str) -> None:
    bucket = _S3_BUCKET
    if not bucket or not storage_key:
        return
    try:
        _get_s3_client().delete_object(Bucket=bucket, Key=storage_key)
    except (BotoCoreError, ClientError) as exc:  # pragma: no cover - defensive cleanup
        logger.warning("Failed to delete S3 object %s: %s", storage_key, exc)


def fetch_asset_bytes(storage_key: str) -> bytes:
    bucket = _require_bucket()
    if not storage_key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Asset file missing")
    client = _get_s3_client()
    try:
        response = client.get_object(Bucket=bucket, Key=storage_key)
    except client.exceptions.NoSuchKey:  # type: ignore[attr-defined]
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Asset file not found")
    except (BotoCoreError, ClientError) as exc:
        logger.error("Failed to download asset %s: %s", storage_key, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Failed to download asset") from exc
    body = response.get("Body")
    data = body.read() if body else b""
    if not data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Asset payload is empty")
    return data


async def persist_upload(
    upload: UploadFile,
    *,
    asset_id: UUID,
    project_id: UUID,
) -> StoredAsset:
    """Upload file contents to S3 after validation."""
    bucket = _require_bucket()
    filename = _sanitize_filename(upload.filename)
    _validate_file_type(filename, upload.content_type)

    data = await upload.read()
    await upload.close()

    size_bytes = len(data)
    if size_bytes == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")
    if size_bytes > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds {_MAX_UPLOAD_MB:.0f} MB limit",
        )

    checksum = hashlib.sha256(data).hexdigest()
    storage_key = _build_storage_key(project_id, asset_id, filename)
    content_type = upload.content_type or "application/octet-stream"

    try:
        _get_s3_client().put_object(
            Bucket=bucket,
            Key=storage_key,
            Body=data,
            ContentType=content_type,
            ContentDisposition=f"inline; filename={filename}",
        )
    except (BotoCoreError, ClientError) as exc:
        logger.error("Failed to store asset %s: %s", storage_key, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Failed to store asset") from exc

    return StoredAsset(
        storage_key=storage_key,
        size_bytes=size_bytes,
        checksum=checksum,
        filename=filename,
        mime_type=content_type,
    )


def upload_limits() -> dict[str, float]:
    return {
        "max_mb": _MAX_UPLOAD_MB,
        "allowed_extensions": sorted(_ALLOWED_EXTENSIONS),
    }

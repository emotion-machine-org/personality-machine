"""Service for handling media asset uploads and retrieval from S3."""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Tuple
from uuid import UUID

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv
from fastapi import HTTPException, UploadFile, status
from PIL import Image

load_dotenv()

logger = logging.getLogger(__name__)

# Configuration
_MAX_UPLOAD_MB = float(os.getenv("CHAT_IMAGES_MAX_SIZE_MB", "10"))
_MAX_UPLOAD_BYTES = int(_MAX_UPLOAD_MB * 1024 * 1024)

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
ALLOWED_IMAGE_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
}

_S3_BUCKET = os.getenv("KNOWLEDGE_S3_BUCKET")  # Reuse same bucket as knowledge assets
_S3_PREFIX = os.getenv("CHAT_IMAGES_S3_PREFIX", "chat-images").strip("/")
_S3_REGION = os.getenv("KNOWLEDGE_S3_REGION") or os.getenv("AWS_REGION")
_PRESIGNED_URL_EXPIRY = int(os.getenv("CHAT_IMAGES_URL_EXPIRY_SECONDS", "3600"))

_S3_CLIENT = None


@dataclass
class StoredMediaAsset:
    """Result of storing a media asset in S3."""

    storage_key: str
    size_bytes: int
    checksum: str
    filename: str
    mime_type: str
    width: int | None = None
    height: int | None = None


def _get_s3_client():
    """Get or create the S3 client."""
    global _S3_CLIENT
    if _S3_CLIENT is None:
        client_kwargs = {}
        if _S3_REGION:
            client_kwargs["region_name"] = _S3_REGION
        _S3_CLIENT = boto3.client("s3", **client_kwargs)
    return _S3_CLIENT


def _require_bucket() -> str:
    """Ensure S3 bucket is configured."""
    if not _S3_BUCKET:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="S3 bucket not configured for media storage",
        )
    return _S3_BUCKET


def _sanitize_filename(filename: str | None) -> str:
    """Sanitize and validate filename."""
    if not filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Filename is required")
    sanitized = Path(filename).name
    if not sanitized:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid filename")
    return sanitized


def _validate_image_type(filename: str, mime_type: str | None) -> None:
    """Validate that the file is an allowed image type."""
    ext = Path(filename).suffix.lower()
    content_type = (mime_type or "").lower() or "application/octet-stream"

    if ext not in ALLOWED_IMAGE_EXTENSIONS and content_type not in ALLOWED_IMAGE_MIME_TYPES:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file type. Allowed: {', '.join(sorted(ALLOWED_IMAGE_EXTENSIONS))}",
        )


def _build_storage_key(
    companion_id: UUID,
    conversation_id: UUID,
    asset_id: UUID,
    filename: str,
) -> str:
    """Build the S3 storage key for an image."""
    safe_name = _sanitize_filename(filename)
    # Pattern: chat-images/{companion_id}/{conversation_id}/{asset_id}.{ext}
    ext = Path(safe_name).suffix.lower()
    key_parts = [str(companion_id), str(conversation_id), f"{asset_id}{ext}"]
    key = "/".join(key_parts)
    if _S3_PREFIX:
        return f"{_S3_PREFIX}/{key}"
    return key


def _extract_image_dimensions(data: bytes, mime_type: str) -> Tuple[int | None, int | None]:
    """Extract width and height from image data."""
    try:
        img = Image.open(BytesIO(data))
        return img.width, img.height
    except Exception as e:
        logger.warning(f"Failed to extract image dimensions: {e}")
        return None, None


async def persist_image_upload(
    upload: UploadFile,
    *,
    asset_id: UUID,
    companion_id: UUID,
    conversation_id: UUID,
) -> StoredMediaAsset:
    """
    Upload an image file to S3 after validation.

    Args:
        upload: The uploaded file
        asset_id: UUID for the new asset
        companion_id: Companion this image belongs to
        conversation_id: Conversation this image is for

    Returns:
        StoredMediaAsset with storage details

    Raises:
        HTTPException: On validation or upload failure
    """
    bucket = _require_bucket()
    filename = _sanitize_filename(upload.filename)
    _validate_image_type(filename, upload.content_type)

    # Read and validate file contents
    data = await upload.read()
    await upload.close()

    size_bytes = len(data)
    if size_bytes == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")
    if size_bytes > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Image exceeds {_MAX_UPLOAD_MB:.0f} MB limit",
        )

    # Calculate checksum and extract dimensions
    checksum = hashlib.sha256(data).hexdigest()
    content_type = upload.content_type or "application/octet-stream"
    width, height = _extract_image_dimensions(data, content_type)

    # Build storage key and upload
    storage_key = _build_storage_key(companion_id, conversation_id, asset_id, filename)

    try:
        _get_s3_client().put_object(
            Bucket=bucket,
            Key=storage_key,
            Body=data,
            ContentType=content_type,
            ContentDisposition=f"inline; filename={filename}",
        )
        logger.info(f"Uploaded image {storage_key} ({size_bytes} bytes)")
    except (BotoCoreError, ClientError) as exc:
        logger.error(f"Failed to store image {storage_key}: {exc}")
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail="Failed to store image",
        ) from exc

    return StoredMediaAsset(
        storage_key=storage_key,
        size_bytes=size_bytes,
        checksum=checksum,
        filename=filename,
        mime_type=content_type,
        width=width,
        height=height,
    )


def generate_presigned_url(
    storage_key: str,
    expiry_seconds: int | None = None,
) -> str:
    """
    Generate a presigned URL for accessing an image.

    Args:
        storage_key: The S3 key for the object
        expiry_seconds: URL expiry time (defaults to env config)

    Returns:
        Presigned URL string
    """
    bucket = _require_bucket()
    expiry = expiry_seconds or _PRESIGNED_URL_EXPIRY

    try:
        url = _get_s3_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": storage_key},
            ExpiresIn=expiry,
        )
        return url
    except (BotoCoreError, ClientError) as exc:
        logger.error(f"Failed to generate presigned URL for {storage_key}: {exc}")
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail="Failed to generate image URL",
        ) from exc


def delete_media_asset(storage_key: str) -> None:
    """
    Delete a media asset from S3.

    Args:
        storage_key: The S3 key for the object
    """
    bucket = _S3_BUCKET
    if not bucket or not storage_key:
        return

    try:
        _get_s3_client().delete_object(Bucket=bucket, Key=storage_key)
        logger.info(f"Deleted media asset {storage_key}")
    except (BotoCoreError, ClientError) as exc:
        logger.warning(f"Failed to delete S3 object {storage_key}: {exc}")


def fetch_media_bytes(storage_key: str) -> bytes:
    """
    Fetch the raw bytes of a media asset from S3.

    Args:
        storage_key: The S3 key for the object

    Returns:
        Raw bytes of the file

    Raises:
        HTTPException: On retrieval failure
    """
    bucket = _require_bucket()
    if not storage_key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Asset file missing")

    client = _get_s3_client()
    try:
        response = client.get_object(Bucket=bucket, Key=storage_key)
    except client.exceptions.NoSuchKey:  # type: ignore[attr-defined]
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Asset file not found")
    except (BotoCoreError, ClientError) as exc:
        logger.error(f"Failed to download asset {storage_key}: {exc}")
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail="Failed to download asset",
        ) from exc

    body = response.get("Body")
    data = body.read() if body else b""
    if not data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Asset payload is empty")
    return data


def upload_limits() -> dict:
    """Return current upload limits for client display."""
    return {
        "max_mb": _MAX_UPLOAD_MB,
        "allowed_extensions": sorted(ALLOWED_IMAGE_EXTENSIONS),
        "allowed_mime_types": sorted(ALLOWED_IMAGE_MIME_TYPES),
    }

from __future__ import annotations

import logging
from typing import Dict, List
from uuid import UUID, uuid4

import asyncpg
from fastapi import HTTPException, UploadFile, status

from ..models.project import KnowledgeAsset, KnowledgeIngestionJob
from ..repositories.project import KnowledgeAssetRepository, KnowledgeIngestionJobRepository
from .knowledge_assets import delete_asset, fetch_asset_bytes, persist_upload
from .knowledge_fixtures import load_known_ingestion_asset
from .openai_vector_store import (
    VectorIngestionFile,
    delete_vector_store_files,
    ensure_companion_vector_store,
    upload_files_to_vector_store,
)

logger = logging.getLogger(__name__)


async def create_asset_from_upload(
    conn: asyncpg.Connection,
    *,
    project_id: UUID,
    companion_id: UUID,
    owner_user_id: UUID | None,
    upload: UploadFile,
) -> KnowledgeAsset:
    asset_id = uuid4()
    stored = await persist_upload(upload, asset_id=asset_id, project_id=project_id)
    try:
        return await KnowledgeAssetRepository.create_asset(
            conn,
            asset_id=asset_id,
            project_id=project_id,
            companion_id=companion_id,
            owner_user_id=owner_user_id,
            filename=stored.filename,
            mime_type=stored.mime_type,
            size_bytes=stored.size_bytes,
            status="uploaded",
            storage_path=stored.storage_key,
            checksum=stored.checksum,
            metadata={"upload_origin": "api" if owner_user_id is None else "dashboard"},
        )
    except Exception:
        delete_asset(stored.storage_key)
        raise


async def _resolve_asset(
    conn: asyncpg.Connection,
    *,
    asset_id: UUID,
    project_id: UUID,
    companion_id: UUID,
) -> KnowledgeAsset:
    asset = await KnowledgeAssetRepository.get_asset_by_id(conn, asset_id)
    if not asset or asset.project_id != project_id or asset.companion_id != companion_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Asset not found")
    return asset


def _payload_type_to_file(payload_type: str) -> tuple[str, str]:
    mapping = {
        "text": ("txt", "text/plain"),
        "markdown": ("md", "text/markdown"),
        "json": ("json", "application/json"),
    }
    return mapping.get((payload_type or "").lower(), ("txt", "text/plain"))


async def ingest_knowledge_payload(
    conn: asyncpg.Connection,
    *,
    project_id: UUID,
    companion_id: UUID,
    payload_type: str,
    inline_content: str | None,
    payload_key: str | None,
    asset_id: UUID | None,
    submitted_by_user: UUID | None,
    submitted_by_key: UUID | None,
    source_label: str,
    missing_key_error: str | None = None,
) -> KnowledgeIngestionJob:
    asset: KnowledgeAsset | None = None
    superseded_assets: List[KnowledgeAsset] = []
    if asset_id:
        asset = await _resolve_asset(
            conn,
            asset_id=asset_id,
            project_id=project_id,
            companion_id=companion_id,
        )
        superseded_assets = await _find_superseded_assets(
            conn,
            companion_id=companion_id,
            filename=asset.filename,
            exclude_asset_id=asset.id,
        )
    elif inline_content:
        # Persist inline content as a lightweight asset for analytics and gating consistency
        filename, mime_type = _payload_type_to_file(payload_type)
        asset_id = uuid4()
        await KnowledgeAssetRepository.create_asset(
            conn,
            asset_id=asset_id,
            project_id=project_id,
            companion_id=companion_id,
            owner_user_id=submitted_by_user,
            filename=f"inline-{asset_id}.{filename}",
            mime_type=mime_type,
            size_bytes=len(inline_content.encode("utf-8")),
            status="ready",
            storage_path=f"inline://{asset_id}",
            checksum=None,
            metadata={
                "ingested_via": source_label,
                "payload_type": payload_type,
                "payload_key": payload_key,
            },
        )
        asset = await KnowledgeAssetRepository.get_asset_by_id(conn, asset_id)

    job_metadata: Dict[str, str | None] = {
        "ingested_via": source_label,
        "payload_type": payload_type,
        "payload_key": payload_key,
    }
    if asset:
        job_metadata["asset_id"] = str(asset.id)

    job = await KnowledgeIngestionJobRepository.create_job(
        conn,
        project_id=project_id,
        companion_id=companion_id,
        source_type=payload_type,
        payload_ref=payload_key,
        submitted_by_user=submitted_by_user,
        submitted_by_key=submitted_by_key,
        asset_id=asset.id if asset else None,
        metadata=job_metadata,
    )

    ingestion_files: List[VectorIngestionFile] = []
    ext, mime = _payload_type_to_file(payload_type)

    if inline_content and inline_content.strip():
        ingestion_files.append(
            VectorIngestionFile(
                filename=f"inline-{job.id}.{ext}",
                mime_type=mime,
                data=inline_content.encode("utf-8"),
                source="inline",
            )
        )

    if payload_key:
        fixture_text = load_known_ingestion_asset(payload_key)
        if not fixture_text:
            await KnowledgeIngestionJobRepository.update_status(
                conn,
                job.id,
                "failed",
                error=missing_key_error or "Unknown ingestion key",
                mark_started=True,
                mark_completed=True,
            )
            refreshed = await KnowledgeIngestionJobRepository.get_job_by_id(conn, job.id)
            assert refreshed is not None
            return refreshed
        ingestion_files.append(
            VectorIngestionFile(
                filename=f"fixture-{payload_key}.{ext}",
                mime_type=mime,
                data=fixture_text.encode("utf-8"),
                source="fixture",
                payload_key=payload_key,
            )
        )

    if asset and not (asset.storage_path or "").startswith("inline://"):
        # Only fetch from S3 for real uploaded assets, not inline content
        await KnowledgeAssetRepository.update_status(conn, asset.id, status="processing")
        asset_bytes = fetch_asset_bytes(asset.storage_path)
        filename = asset.filename
        if filename.lower().endswith(".jsonl"):
            filename = filename[:-6] + ".json"
        ingestion_files.append(
            VectorIngestionFile(
                filename=filename,
                mime_type=asset.mime_type,
                data=asset_bytes,
                source="asset",
                asset_id=asset.id,
            )
        )

    if not ingestion_files:
        await KnowledgeIngestionJobRepository.update_status(
            conn,
            job.id,
            "failed",
            error="No content provided",
            mark_started=True,
            mark_completed=True,
        )
        return await KnowledgeIngestionJobRepository.get_job_by_id(conn, job.id)  # type: ignore[return-value]

    try:
        vector_store_id = await ensure_companion_vector_store(conn, companion_id=companion_id)
        await _record_job_metadata(conn, job.id, {"vector_store_id": vector_store_id})

        await KnowledgeIngestionJobRepository.update_status(
            conn,
            job.id,
            "running",
            mark_started=True,
        )
        upload_results = await upload_files_to_vector_store(vector_store_id, ingestion_files)

        if superseded_assets and asset:
            await _purge_superseded_assets(
                conn,
                vector_store_id=vector_store_id,
                superseded_assets=superseded_assets,
                replacing_asset_id=asset.id,
            )

        metadata_updates: Dict[str, object] = {
            "files": [res.to_metadata() for res in upload_results],
            "vector_store_id": vector_store_id,
        }
        if superseded_assets and asset:
            metadata_updates["superseded_asset_ids"] = [str(item.id) for item in superseded_assets]

        for res in upload_results:
            if res.asset_id:
                await KnowledgeAssetRepository.update_status(
                    conn,
                    res.asset_id,
                    status="ready",
                    metadata={
                        "openai_file_id": res.openai_file_id,
                        "vector_store_file_id": res.vector_store_file_id,
                        "bytes": res.bytes,
                        "last_ingested_job_id": str(job.id),
                    },
                )

        await KnowledgeIngestionJobRepository.update_status(
            conn,
            job.id,
            "succeeded",
            mark_completed=True,
        )
        await _record_job_metadata(conn, job.id, metadata_updates)
        refreshed = await KnowledgeIngestionJobRepository.get_job_by_id(conn, job.id)
        assert refreshed is not None
        return refreshed
    except HTTPException as exc:
        if asset:
            await KnowledgeAssetRepository.update_status(
                conn,
                asset.id,
                status="failed",
                metadata={"error": exc.detail if isinstance(exc.detail, str) else str(exc.detail)},
            )
        await KnowledgeIngestionJobRepository.update_status(
            conn,
            job.id,
            "failed",
            error=exc.detail if isinstance(exc.detail, str) else str(exc),
            mark_completed=True,
        )
        raise
    except Exception as exc:
        if asset:
            await KnowledgeAssetRepository.update_status(
                conn,
                asset.id,
                status="failed",
                metadata={"error": str(exc)},
            )
        await KnowledgeIngestionJobRepository.update_status(
            conn,
            job.id,
            "failed",
            error=str(exc),
            mark_completed=True,
        )
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to ingest knowledge"
        )


async def _record_job_metadata(
    conn: asyncpg.Connection,
    job_id: UUID,
    metadata: Dict[str, object],
) -> None:
    # Append metadata map onto existing jsonb document (client callers expect job metadata to be JSON).
    # Uses unified 'jobs' table - metadata is stored in params->'metadata'
    # Handle cases where params might be NULL, JSON null, or not have a metadata key
    # Note: Don't use json.dumps here - asyncpg's JSONB codec handles encoding automatically
    await conn.execute(
        """
        UPDATE jobs
        SET params = jsonb_set(
            CASE WHEN params IS NULL OR jsonb_typeof(params) != 'object'
                 THEN '{}'::jsonb ELSE params END,
            '{metadata}',
            COALESCE(params->'metadata', '{}'::jsonb) || $2
        )
        WHERE id = $1 AND job_type = 'knowledge_ingestion'
        """,
        job_id,
        metadata,  # Pass dict directly - asyncpg's JSONB codec handles encoding
    )


def _extract_vector_store_file_id(asset: KnowledgeAsset) -> str | None:
    metadata = asset.metadata or {}
    file_id = metadata.get("vector_store_file_id")
    if isinstance(file_id, str) and file_id.strip():
        return file_id
    return None


async def _find_superseded_assets(
    conn: asyncpg.Connection,
    *,
    companion_id: UUID,
    filename: str | None,
    exclude_asset_id: UUID | None,
) -> List[KnowledgeAsset]:
    if not filename:
        return []
    assets = await KnowledgeAssetRepository.list_assets_by_filename(
        conn,
        companion_id=companion_id,
        filename=filename,
    )
    superseded: List[KnowledgeAsset] = []
    for item in assets:
        if exclude_asset_id and item.id == exclude_asset_id:
            continue
        if _extract_vector_store_file_id(item):
            superseded.append(item)
    return superseded


async def _purge_superseded_assets(
    conn: asyncpg.Connection,
    *,
    vector_store_id: str,
    superseded_assets: List[KnowledgeAsset],
    replacing_asset_id: UUID,
) -> None:
    if not superseded_assets:
        return

    file_ids = [
        file_id for asset in superseded_assets if (file_id := _extract_vector_store_file_id(asset))
    ]
    if not file_ids:
        return

    await delete_vector_store_files(vector_store_id, file_ids)

    for asset in superseded_assets:
        await KnowledgeAssetRepository.update_status(
            conn,
            asset.id,
            status="superseded",
            metadata={"superseded_by_asset_id": str(replacing_asset_id)},
        )


async def delete_knowledge_asset(
    conn: asyncpg.Connection,
    *,
    asset_id: UUID,
    companion_id: UUID,
) -> bool:
    """
    Delete a knowledge asset and its corresponding file from OpenAI vector store.

    Returns True if asset was deleted, False if not found.
    """
    asset = await KnowledgeAssetRepository.get_asset_by_id(conn, asset_id)
    if not asset or asset.companion_id != companion_id:
        return False

    file_id = _extract_vector_store_file_id(asset)
    if file_id:
        vector_store_id = await ensure_companion_vector_store(conn, companion_id=companion_id)
        try:
            await delete_vector_store_files(vector_store_id, [file_id])
        except HTTPException:
            logger.warning(
                "Failed to delete vector store file %s for asset %s, continuing with DB deletion",
                file_id,
                asset_id,
            )

    return await KnowledgeAssetRepository.delete_asset(conn, asset_id, companion_id)

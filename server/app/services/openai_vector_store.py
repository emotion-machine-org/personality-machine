from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence
from uuid import UUID

from fastapi import HTTPException, status
from openai import NOT_GIVEN

from ..repositories.companion import CompanionRepository
from .openai_clients import get_openai_async_client

_DEFAULT_RANKER = "default-2024-11-15"
_DEFAULT_SCORE_THRESHOLD = 0.2
_DEFAULT_HYBRID_EMBED = 0.7
_DEFAULT_HYBRID_TEXT = 0.3
_VALID_SEARCH_MODES = {"hybrid", "semantic", "keyword"}


@dataclass
class VectorIngestionFile:
    filename: str
    mime_type: str
    data: bytes
    source: str
    asset_id: UUID | None = None
    payload_key: str | None = None


@dataclass
class VectorIngestionResult:
    source: str
    asset_id: UUID | None
    payload_key: str | None
    openai_file_id: str
    vector_store_file_id: str
    bytes: int

    def to_metadata(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "asset_id": str(self.asset_id) if self.asset_id else None,
            "payload_key": self.payload_key,
            "openai_file_id": self.openai_file_id,
            "vector_store_file_id": self.vector_store_file_id,
            "bytes": self.bytes,
        }


async def ensure_companion_vector_store(
    conn,
    *,
    companion_id: UUID,
) -> str:
    existing = await CompanionRepository.get_vector_store_id(conn, companion_id)
    if existing:
        return existing

    client = get_openai_async_client()
    vs_name = f"companion-{companion_id}"
    try:
        resp = await client.vector_stores.create(name=vs_name)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Failed to create vector store: {exc}"
        ) from exc

    await CompanionRepository.set_vector_store_id(conn, companion_id, resp.id)
    return resp.id


async def upload_files_to_vector_store(
    vector_store_id: str,
    files: List[VectorIngestionFile],
) -> List[VectorIngestionResult]:
    if not files:
        return []

    client = get_openai_async_client()
    results: List[VectorIngestionResult] = []

    for item in files:
        buffer = io.BytesIO(item.data)
        buffer.name = item.filename
        try:
            resp = await client.vector_stores.files.upload_and_poll(
                vector_store_id=vector_store_id,
                file=buffer,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to ingest file '{item.filename}': {exc}",
            ) from exc

        status_value = getattr(resp, "status", None)
        if status_value and status_value.lower() != "completed":
            last_error = getattr(resp, "last_error", None)
            msg = (
                last_error.get("message")
                if isinstance(last_error, dict)
                else last_error or "Unknown error"
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"OpenAI reported '{status_value}' while ingesting '{item.filename}': {msg}",
            )

        results.append(
            VectorIngestionResult(
                source=item.source,
                asset_id=item.asset_id,
                payload_key=item.payload_key,
                openai_file_id=getattr(resp, "file_id", None) or getattr(resp, "id", None),
                vector_store_file_id=getattr(resp, "id", None),
                bytes=getattr(resp, "bytes", 0) or 0,
            )
        )

    return results


async def delete_vector_store_files(
    vector_store_id: str,
    file_ids: Sequence[str],
) -> None:
    if not file_ids:
        return

    client = get_openai_async_client()
    for file_id in file_ids:
        if not file_id:
            continue
        try:
            await client.vector_stores.files.delete(
                vector_store_id=vector_store_id,
                file_id=file_id,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to delete vector store file '{file_id}': {exc}",
            ) from exc


def _ranking_options(mode: str | None = None) -> Any:
    ranker = _DEFAULT_RANKER
    score_threshold_raw = _DEFAULT_SCORE_THRESHOLD
    hybrid_embed_raw = _DEFAULT_HYBRID_EMBED
    hybrid_text_raw = _DEFAULT_HYBRID_TEXT

    options: Dict[str, Any] = {}
    if ranker:
        options["ranker"] = ranker
    score_value = score_threshold_raw
    if score_value is None:
        score_value = _DEFAULT_SCORE_THRESHOLD
    try:
        options["score_threshold"] = max(0.0, min(1.0, float(score_value)))
    except ValueError:
        pass
    selected_mode = (mode or "hybrid").lower()
    if selected_mode not in _VALID_SEARCH_MODES:
        selected_mode = "hybrid"

    hybrid: Dict[str, Any] = {}
    if selected_mode == "semantic":
        embed_float = 1.0
        text_float = 0.0
    elif selected_mode == "keyword":
        embed_float = 0.0
        text_float = 1.0
    else:
        embed_value = hybrid_embed_raw if hybrid_embed_raw is not None else _DEFAULT_HYBRID_EMBED
        text_value = hybrid_text_raw if hybrid_text_raw is not None else _DEFAULT_HYBRID_TEXT
        try:
            embed_float = max(0.0, float(embed_value))
        except ValueError:
            embed_float = 0.0
        try:
            text_float = max(0.0, float(text_value))
        except ValueError:
            text_float = 0.0

    if embed_float > 0:
        hybrid["embedding_weight"] = embed_float
    if text_float > 0:
        hybrid["text_weight"] = text_float
    if hybrid:
        options["hybrid_search"] = hybrid

    return options or NOT_GIVEN


async def search_vector_store(
    conn,
    *,
    companion_id: UUID,
    query: str,
    max_results: int = 5,
    filters: Dict[str, Any] | None = None,
    mode: str | None = None,
) -> List[Dict[str, Any]]:
    vector_store_id = await ensure_companion_vector_store(conn, companion_id=companion_id)
    client = get_openai_async_client()
    ranking_opts = _ranking_options(mode=mode)

    kwargs: Dict[str, Any] = {}
    if filters:
        kwargs["filters"] = filters
    if ranking_opts is not NOT_GIVEN:
        kwargs["ranking_options"] = ranking_opts

    try:
        resp = await client.vector_stores.search(
            vector_store_id=vector_store_id,
            query=query,
            max_num_results=max(1, max_results),
            **kwargs,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Knowledge search failed: {exc}"
        ) from exc

    results: List[Dict[str, Any]] = []
    for item in getattr(resp, "data", []) or []:
        texts: List[str] = []
        for block in getattr(item, "content", []) or []:
            if getattr(block, "type", None) == "text":
                texts.append(getattr(block, "text", ""))
        results.append(
            {
                "file_id": getattr(item, "file_id", None),
                "filename": getattr(item, "filename", None),
                "score": getattr(item, "score", None),
                "text": "\n".join(t for t in texts if t),
                "attributes": getattr(item, "attributes", None),
            }
        )

    return results

#!/usr/bin/env python3
"""Clone a companion inside the same project.

Creates a new companion that mirrors an existing one — copying the latest
DEPLOYED version (effective prompt + config), the active deployment slug,
behavior links, and knowledge-asset references. The new companion lives
under the same owner/project as the source.

Vector store handling (default: duplicate):
  The source companion's OpenAI vector store is duplicated — a new vector
  store is created and the same underlying OpenAI File objects are
  attached to it. Two independent indexes, one underlying file. Prod and
  test can diverge (add/remove docs) without affecting each other.
  Use --share-vector-store to skip duplication and reuse the source vs
  instead. Use --strip-vector-store to clear it entirely (fresh ingest
  required later).

Per-user memory (memories, memory_v2_entries) is keyed by relationship_id
and is therefore automatically isolated — no cloning needed.

Usage:
    cd server
    uv run python app/scripts/clone_companion.py \\
        --source 9b5a07f1-3008-43ab-975b-647c354969c7 \\
        --name "My Companion (Test)"

    # Dry run — DB rolled back, no OpenAI calls at all
    uv run python app/scripts/clone_companion.py \\
        --source 9b5a07f1-... --name "My Companion (Test)" --dry-run

    # Share source vs instead of duplicating (read-only index shared)
    uv run python app/scripts/clone_companion.py \\
        --source 9b5a07f1-... --name "My Companion (Test)" --share-vector-store

Environment:
    DATABASE_DSN   - PostgreSQL connection string (required)
    OPENAI_API_KEY - OpenAI key (required unless --skip-vector-store-ops
                     or --share-vector-store or --strip-vector-store)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import Json, RealDictCursor

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

ENV_PATH = ROOT_DIR / ".env"
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", required=True, help="Source companion UUID to clone")
    p.add_argument("--name", required=True, help="New companion name")
    p.add_argument(
        "--skip-knowledge",
        action="store_true",
        help="Do not duplicate knowledge_assets rows (knowledge will be empty on the new companion)",
    )
    vs_group = p.add_mutually_exclusive_group()
    vs_group.add_argument(
        "--share-vector-store",
        action="store_true",
        help="Reuse the source vector_store_id (shared read-only index) instead of duplicating",
    )
    vs_group.add_argument(
        "--strip-vector-store",
        action="store_true",
        help="Clear vector_store_id from cloned metadata (forces fresh KB ingestion later)",
    )
    p.add_argument(
        "--skip-vector-store-ops",
        action="store_true",
        help="Do not call OpenAI (useful in dry runs); metadata is left as source's vs id",
    )
    p.add_argument("--dry-run", action="store_true", help="Run inside a rolled-back transaction")
    return p.parse_args()


async def duplicate_vector_store(
    *,
    source_vs_id: str,
    new_companion_id: UUID,
) -> tuple[str, dict[str, str]]:
    """Create a new OpenAI vector store mirroring the source.

    Re-attaches the source's underlying File objects to a new vector store
    so the new companion gets its own independent index over the same
    underlying file(s). Returns (new_vs_id, {old_vs_file_id: new_vs_file_id}).
    """
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])

    source_files: list[Any] = []
    cursor = None
    while True:
        kwargs: dict[str, Any] = {"limit": 100}
        if cursor:
            kwargs["after"] = cursor
        page = await client.vector_stores.files.list(source_vs_id, **kwargs)
        data = getattr(page, "data", []) or []
        source_files.extend(data)
        if not getattr(page, "has_more", False):
            break
        cursor = data[-1].id if data else None
        if cursor is None:
            break

    file_ids = [getattr(f, "file_id", None) or getattr(f, "id", None) for f in source_files]
    file_ids = [fid for fid in file_ids if fid]

    new_vs = await client.vector_stores.create(name=f"companion-{new_companion_id}")

    remap: dict[str, str] = {}
    if file_ids:
        batch = await client.vector_stores.file_batches.create_and_poll(
            vector_store_id=new_vs.id,
            file_ids=file_ids,
        )
        batch_status = getattr(batch, "status", None)
        if batch_status and batch_status.lower() != "completed":
            raise RuntimeError(
                f"Vector store batch indexing ended with status '{batch_status}' "
                f"(source vs={source_vs_id}, new vs={new_vs.id})"
            )
        # Build remap: old vs_file.id -> new vs_file.id for matching file_id
        new_files = []
        cursor = None
        while True:
            kwargs = {"limit": 100}
            if cursor:
                kwargs["after"] = cursor
            page = await client.vector_stores.files.list(new_vs.id, **kwargs)
            data = getattr(page, "data", []) or []
            new_files.extend(data)
            if not getattr(page, "has_more", False):
                break
            cursor = data[-1].id if data else None
            if cursor is None:
                break
        new_by_file_id = {getattr(f, "file_id", None): getattr(f, "id", None) for f in new_files}
        for src in source_files:
            src_file_id = getattr(src, "file_id", None)
            src_vs_file_id = getattr(src, "id", None)
            new_vs_file_id = new_by_file_id.get(src_file_id)
            if src_vs_file_id and new_vs_file_id:
                remap[src_vs_file_id] = new_vs_file_id

    return new_vs.id, remap


def fetch_one(cur, sql: str, params: tuple) -> dict | None:
    cur.execute(sql, params)
    row = cur.fetchone()
    return dict(row) if row else None


def fetch_all(cur, sql: str, params: tuple) -> list[dict]:
    cur.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


def clone(
    conn,
    *,
    source_id: UUID,
    new_name: str,
    skip_knowledge: bool,
    share_vector_store: bool,
    strip_vector_store: bool,
) -> tuple[UUID, str, dict[str, Any]]:
    """Run the SQL portion of the clone. Returns (new_id, slug, vs_plan).

    vs_plan: {"mode": "share"|"strip"|"duplicate"|"none", "source_vs_id": str|None}
    The caller is responsible for any OpenAI work after commit.
    """
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # 1. Source companion
    source = fetch_one(
        cur,
        "SELECT id, owner_id, project_id, description, metadata FROM companions WHERE id = %s",
        (str(source_id),),
    )
    if not source:
        raise SystemExit(f"Source companion {source_id} not found")

    source_metadata = dict(source["metadata"] or {})
    source_vs_id = source_metadata.get("vector_store_id")

    # Decide vs plan. The new companion is inserted with a placeholder value
    # for vector_store_id (source's id or none), and we fix it up after commit
    # if we're duplicating. That avoids orphaning a vs if the DB errors.
    if strip_vector_store:
        vs_mode = "strip"
        metadata = {k: v for k, v in source_metadata.items() if k != "vector_store_id"}
    elif share_vector_store or not source_vs_id:
        vs_mode = "share" if source_vs_id else "none"
        metadata = dict(source_metadata)
    else:
        vs_mode = "duplicate"
        # Leave source vs_id in metadata for now; post-commit step will replace it.
        metadata = dict(source_metadata)

    # 2. Latest DEPLOYED version
    version = fetch_one(
        cur,
        """
        SELECT id, system_prompt, voice_id, memory_enabled,
               memory_evaluation_prompt, effective_system_prompt, config
        FROM companion_versions
        WHERE companion_id = %s AND status = 'DEPLOYED'
        ORDER BY version_number DESC
        LIMIT 1
        """,
        (str(source_id),),
    )
    if not version:
        raise SystemExit(f"No DEPLOYED version found for {source_id}")

    # 3. Insert new companion
    new_companion_id = uuid4()
    cur.execute(
        """
        INSERT INTO companions (id, owner_id, project_id, name, description, metadata)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            str(new_companion_id),
            source["owner_id"],
            source["project_id"],
            new_name,
            source["description"],
            Json(metadata),
        ),
    )

    # 4. Insert new version (trigger auto-numbers to 1)
    new_version_id = uuid4()
    cur.execute(
        """
        INSERT INTO companion_versions (
            id, companion_id, system_prompt, voice_id, memory_enabled,
            memory_evaluation_prompt, effective_system_prompt, config, status
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'DEPLOYED')
        """,
        (
            str(new_version_id),
            str(new_companion_id),
            version["system_prompt"],
            version["voice_id"],
            version["memory_enabled"],
            version["memory_evaluation_prompt"],
            version["effective_system_prompt"],
            Json(version["config"]) if version["config"] is not None else None,
        ),
    )

    # 5. Deployment (match existing slug format: companion-{cid}-v{vid})
    new_deployment_id = uuid4()
    slug = f"companion-{new_companion_id}-v{new_version_id}"
    cur.execute(
        """
        INSERT INTO deployments (id, companion_id, version_id, slug, is_active)
        VALUES (%s, %s, %s, %s, TRUE)
        """,
        (str(new_deployment_id), str(new_companion_id), str(new_version_id), slug),
    )

    # 6. Copy behavior links (reuse behavior_ids — same project)
    behavior_links = fetch_all(
        cur,
        """
        SELECT behavior_id, triggers, classifier_eligible, classifier_hint,
               priority, webhook_url, webhook_secret, params, enabled, isolated
        FROM companion_behavior_links
        WHERE companion_id = %s AND relationship_id IS NULL
        """,
        (str(source_id),),
    )
    for link in behavior_links:
        cur.execute(
            """
            INSERT INTO companion_behavior_links (
                companion_id, behavior_id, triggers, classifier_eligible, classifier_hint,
                priority, webhook_url, webhook_secret, params, enabled, isolated
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                str(new_companion_id),
                link["behavior_id"],
                Json(link["triggers"]),
                link["classifier_eligible"],
                link["classifier_hint"],
                link["priority"],
                link["webhook_url"],
                link["webhook_secret"],
                Json(link["params"]) if link["params"] is not None else None,
                link["enabled"],
                link["isolated"],
            ),
        )

    # 7. Knowledge assets (share storage_path; the companion_id FK is what
    #    the companion-scoped search uses. Shared corpus, independent row.)
    knowledge_count = 0
    if not skip_knowledge:
        assets = fetch_all(
            cur,
            """
            SELECT owner_user_id, filename, mime_type, size_bytes, status,
                   storage_path, checksum, metadata
            FROM knowledge_assets
            WHERE companion_id = %s
            """,
            (str(source_id),),
        )
        for asset in assets:
            asset_metadata = dict(asset["metadata"] or {})
            cur.execute(
                """
                INSERT INTO knowledge_assets (
                    project_id, companion_id, owner_user_id, filename, mime_type,
                    size_bytes, status, storage_path, checksum, metadata
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    source["project_id"],
                    str(new_companion_id),
                    asset["owner_user_id"],
                    asset["filename"],
                    asset["mime_type"],
                    asset["size_bytes"],
                    asset["status"],
                    asset["storage_path"],
                    asset["checksum"],
                    Json(asset_metadata) if asset_metadata else None,
                ),
            )
            knowledge_count += 1

    print("== Clone summary (SQL) ==")
    print(f"  source companion:   {source_id}")
    print(f"  new companion:      {new_companion_id}")
    print(f"  new version:        {new_version_id}")
    print(f"  deployment slug:    {slug}")
    print(f"  project_id:         {source['project_id']}")
    print(f"  owner_id:           {source['owner_id']}")
    print(f"  behavior links:     {len(behavior_links)}")
    print(f"  knowledge assets:   {knowledge_count}")
    print(f"  source vs:          {source_vs_id or '(none)'}")
    print(f"  vs plan:            {vs_mode}")

    return new_companion_id, slug, {"mode": vs_mode, "source_vs_id": source_vs_id}


def apply_vs_duplication(
    conn,
    *,
    new_companion_id: UUID,
    source_vs_id: str,
) -> str:
    """Duplicate the vector store (OpenAI side) and patch DB metadata.

    Runs after the main DB transaction commits. On failure, the companion
    still exists pointing at the source vs — re-run manually or use
    --share-vector-store to leave it that way.
    """
    new_vs_id, vs_file_remap = asyncio.run(
        duplicate_vector_store(
            source_vs_id=source_vs_id,
            new_companion_id=new_companion_id,
        )
    )
    cur = conn.cursor(cursor_factory=RealDictCursor)
    # Patch companion metadata.vector_store_id
    cur.execute(
        """
        UPDATE companions
        SET metadata = jsonb_set(metadata, '{vector_store_id}', to_jsonb(%s::text))
        WHERE id = %s
        """,
        (new_vs_id, str(new_companion_id)),
    )
    # Patch knowledge_assets metadata vector_store_file_id references
    if vs_file_remap:
        assets = fetch_all(
            cur,
            "SELECT id, metadata FROM knowledge_assets WHERE companion_id = %s",
            (str(new_companion_id),),
        )
        for asset in assets:
            md = dict(asset["metadata"] or {})
            old = md.get("vector_store_file_id")
            if old and old in vs_file_remap:
                md["vector_store_file_id"] = vs_file_remap[old]
                cur.execute(
                    "UPDATE knowledge_assets SET metadata = %s WHERE id = %s",
                    (Json(md), asset["id"]),
                )
    conn.commit()
    return new_vs_id


def main() -> int:
    args = parse_args()
    dsn = os.environ.get("DATABASE_DSN")
    if not dsn:
        raise SystemExit("DATABASE_DSN is not set")

    source_id = UUID(args.source)
    conn = psycopg2.connect(dsn)
    try:
        new_id, slug, vs_plan = clone(
            conn,
            source_id=source_id,
            new_name=args.name,
            skip_knowledge=args.skip_knowledge,
            share_vector_store=args.share_vector_store,
            strip_vector_store=args.strip_vector_store,
        )
        if args.dry_run:
            conn.rollback()
            print("\n[dry-run] DB transaction rolled back; no OpenAI calls made")
            return 0

        conn.commit()
        print(f"\nDB committed. New companion: {new_id}")

        if vs_plan["mode"] == "duplicate" and not args.skip_vector_store_ops:
            print(f"Duplicating vector store {vs_plan['source_vs_id']} ...")
            new_vs = apply_vs_duplication(
                conn,
                new_companion_id=new_id,
                source_vs_id=vs_plan["source_vs_id"],
            )
            print(f"Vector store duplicated -> {new_vs}")
        elif vs_plan["mode"] == "duplicate":
            print(
                f"[!] Skipping vector store duplication per --skip-vector-store-ops. "
                f"Companion still points at source vs {vs_plan['source_vs_id']}."
            )

        print(f"\nDone. Deployment slug: {slug}")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Manual script to upload and ingest sample files via the Emotion Machine API.

Usage:
    EM_PROJECT_API_KEY=... EM_TEST_COMPANION_ID=... python server/tests/knowledge_ingestion_sanity.py

Optional envs:
    EM_BASE_URL (default http://localhost:8100)
    EM_INGEST_POLL_SECONDS (default 0.5)
    EM_INGEST_TIMEOUT_SECONDS (default 60)
"""

from __future__ import annotations

import json
import mimetypes
import os
import sys
import time
from pathlib import Path
from typing import Dict, Tuple

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("EM_BASE_URL", "http://localhost:8100").rstrip("/")
API_KEY = os.getenv("TEST_EM_API_KEY")
COMPANION_ID = os.getenv("EM_TEST_COMPANION_ID", "b2f695c7-3bb2-4f90-a5eb-965b8e11bfd1")
CUSTOM_FILE = str(
    Path(__file__).resolve().parents[1] / "examples" / "cycle_companion_knowledge.jsonl"
)
CUSTOM_FILE_TYPE = "json"  # os.getenv("EM_CUSTOM_KNOWLEDGE_FILE_TYPE", "text")
CUSTOM_QUERIES = os.getenv("EM_CUSTOM_KNOWLEDGE_QUERIES")
POLL_INTERVAL = float(os.getenv("EM_INGEST_POLL_SECONDS", "0.5"))
POLL_TIMEOUT = float(os.getenv("EM_INGEST_TIMEOUT_SECONDS", "120"))
REQUEST_TIMEOUT = float(os.getenv("EM_INGEST_REQUEST_TIMEOUT_SECONDS", "180"))
HEADERS = {"Authorization": f"Bearer {API_KEY}"}
SEARCH_MODES = ["hybrid", "semantic", "keyword"]


def _require_envs() -> None:
    missing = [
        name
        for name, value in {
            "EM_PROJECT_API_KEY": API_KEY,
            "EM_TEST_COMPANION_ID": COMPANION_ID,
        }.items()
        if not value
    ]
    if missing:
        sys.exit(f"Missing required environment variables: {', '.join(missing)}")


def _client() -> httpx.Client:
    return httpx.Client(base_url=BASE_URL, headers=HEADERS, timeout=REQUEST_TIMEOUT)


def _debug_response(label: str, resp: httpx.Response) -> None:
    try:
        payload = resp.json()
    except ValueError:
        payload = resp.text
    print(f"[{label}] {resp.status_code} response: {payload}", file=sys.stderr)


def _upload_asset(client: httpx.Client, *, filename: str, mime: str, content: bytes) -> Dict:
    url = f"/v1/companions/{COMPANION_ID}/knowledge"
    files = {"file": (filename, content, mime)}
    data = {"type": CUSTOM_FILE_TYPE or "text"}
    resp = client.post(url, files=files, data=data)
    if resp.status_code >= 400:
        _debug_response("ingest_file", resp)
        resp.raise_for_status()
    job = resp.json()
    print(f"Ingestion job created from file {filename}: {job.get('id')}")
    return job


def _poll_job(client: httpx.Client, job_id: str) -> Dict:
    status_url = f"/v1/knowledge-jobs/{job_id}"
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        poll = client.get(status_url)
        if poll.status_code >= 400:
            _debug_response("poll", poll)
            poll.raise_for_status()
        body = poll.json()
        status_value = body["status"].lower()
        if status_value in {"succeeded", "failed"}:
            print(f"Job {job_id} completed with status={status_value}")
            if body.get("error"):
                print(f"Error: {body['error']}")
            return body
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"Job {job_id} did not finish within {POLL_TIMEOUT}s")


def _search_knowledge(
    client: httpx.Client,
    query: str,
    *,
    max_results: int = 5,
    mode: str = "hybrid",
) -> Dict:
    url = f"/v1/companions/{COMPANION_ID}/knowledge/search"
    payload = {"query": query, "max_results": max_results, "mode": mode}
    resp = client.post(url, json=payload)
    if resp.status_code >= 400:
        _debug_response("search", resp)
        resp.raise_for_status()
    result = resp.json()
    print(f"Search query ({mode}): {query}")
    for idx, item in enumerate(result.get("results", []), start=1):
        snippet = (item.get("text") or "")[:200].replace("\n", " ")
        print(f"  [{idx}] score={item.get('score')} file={item.get('filename')} -> {snippet}...")
    print("-" * 80)
    return result


def _generate_payloads() -> Dict[str, Tuple[str, bytes]]:
    if CUSTOM_FILE:
        path = Path(CUSTOM_FILE)
        if not path.is_file():
            sys.exit(f"Custom file {CUSTOM_FILE} not found")
        with path.open("rb") as handle:
            data = handle.read()
        mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        return {path.name: (mime, data)}

    json_blob = {
        "title": "Human emotions dataset",
        "entries": [
            {
                "id": idx,
                "emotion": emotion,
                "notes": "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 20,
            }
            for idx, emotion in enumerate(
                ["happy", "sad", "excited", "calm", "curious", "grateful", "resilient"], start=1
            )
        ],
    }
    large_json = json.dumps(json_blob, indent=2).encode()

    markdown_sections = [
        f"## Section {i}\n\n"
        + ("This is a markdown paragraph with bullet points.\n- item A\n- item B\n\n" * 3)
        for i in range(1, 10)
    ]
    large_md = ("# Companion Knowledge Base\n\n" + "\n".join(markdown_sections)).encode()

    text_block = (
        "User preference snapshot:\n"
        + "; ".join([f"pref_{i}=value_{i}" for i in range(1, 200)])
        + "\n"
    ) * 30
    large_txt = text_block.encode()

    return {
        "large.json": ("application/json", large_json),
        "notes.md": ("text/markdown", large_md),
        "summary.txt": ("text/plain", large_txt),
    }


def main() -> None:
    _require_envs()
    payloads = _generate_payloads()
    if CUSTOM_QUERIES:
        retrieval_queries = [q.strip() for q in CUSTOM_QUERIES.split("||") if q.strip()]
    else:
        retrieval_queries = [
            # "When during the menstrual cycle do salt cravings peak?",
            # "Explain the hormonal mechanism driving salt cravings in the luteal phase.",
            # "Which lifestyle factors make sugar cravings worse before a period?",
            # "How many extra calories can dessert cravings add according to the research summary?",
            # "What prevalence ranges are reported for salt versus sugar cravings?",
            "I'm in my 16th day i feel bad, why is that?"
        ]
    with _client() as client:
        for filename, (mime, content) in payloads.items():
            job = _upload_asset(client, filename=filename, mime=mime, content=content)
            if job.get("id"):
                job = _poll_job(client, job["id"])
            print(json.dumps(job, indent=2))
            print("-" * 80)
        for mode in SEARCH_MODES:
            print(f"=== Search mode: {mode} ===")
            for query in retrieval_queries:
                _search_knowledge(client, query=query, max_results=5, mode=mode)


if __name__ == "__main__":
    main()

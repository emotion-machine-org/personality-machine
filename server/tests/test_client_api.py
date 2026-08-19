import json
import os
import time
from http import HTTPStatus

import httpx
import pytest
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("EM_BASE_URL", "http://localhost:8100")
API_KEY = os.getenv("TEST_EM_API_KEY")
# Hard-coded companion/project context; adjust as needed.
COMPANION_ID = "b2f695c7-3bb2-4f90-a5eb-965b8e11bfd1"
FOREIGN_COMPANION_ID = "272e5c29-c05b-4c2e-b8ef-a55a0b36b961"


pytestmark = pytest.mark.skipif(
    not (API_KEY and COMPANION_ID),
    reason="Set EM_PROJECT_API_KEY and EM_TEST_COMPANION_ID to run integration tests.",
)


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }


def test_profile_schema_roundtrip_live():
    url = f"{BASE_URL}/v1/companions/{COMPANION_ID}/profile-schema"
    headers = _headers()

    with httpx.Client(timeout=20.0) as client:
        original = client.get(url, headers=headers)
        original_schema = (
            original.json().get("schema") if original.status_code == HTTPStatus.OK else None
        )

        payload = {
            "schema": {"type": "object", "properties": {"favorite_color": {"type": "string"}}}
        }
        upsert = client.put(url, headers=headers, json=payload)
        assert upsert.status_code == HTTPStatus.OK
        read_back = client.get(url, headers=headers)
        assert read_back.status_code == HTTPStatus.OK
        read_schema = read_back.json()["schema"]
        assert read_schema["properties"]["favorite_color"]["type"] == "string"

        if original_schema is not None:
            client.put(url, headers=headers, json={"schema": original_schema})


def test_knowledge_ingestion_job_live():
    ingest_url = f"{BASE_URL}/v1/companions/{COMPANION_ID}/knowledge"
    headers = _headers()

    with httpx.Client(timeout=20.0) as client:
        response = client.post(
            ingest_url,
            headers=headers,
            json={"type": "text", "content": "Integration test snippet."},
        )
        assert response.status_code == HTTPStatus.ACCEPTED
        job = response.json()
        job_id = job["id"]

        status_url = f"{BASE_URL}/v1/knowledge-jobs/{job_id}"
        deadline = time.time() + 20
        last_payload = job
        while time.time() < deadline:
            poll = client.get(status_url, headers=headers)
            assert poll.status_code == HTTPStatus.OK
            last_payload = poll.json()
            if last_payload["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.5)

        assert last_payload["status"] == "succeeded", f"Job failed: {last_payload}"


@pytest.mark.skipif(
    not (API_KEY and COMPANION_ID),
    reason="Set EM_PROJECT_API_KEY and EM_TEST_COMPANION_ID to run integration tests.",
)
def test_knowledge_ingestion_job_with_key_live():
    ingest_url = f"{BASE_URL}/v1/companions/{COMPANION_ID}/knowledge"
    headers = _headers()

    with httpx.Client(timeout=20.0) as client:
        response = client.post(
            ingest_url,
            headers=headers,
            json={"type": "json", "key": "cycle_companion_reference_v1"},
        )
        assert response.status_code == HTTPStatus.ACCEPTED
        job = response.json()
        job_id = job["id"]

        status_url = f"{BASE_URL}/v1/knowledge-jobs/{job_id}"
        deadline = time.time() + 20
        last_payload = job
        while time.time() < deadline:
            poll = client.get(status_url, headers=headers)
            assert poll.status_code == HTTPStatus.OK
            last_payload = poll.json()
            if last_payload["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.5)

        assert last_payload["status"] == "succeeded", f"Job failed: {last_payload}"


@pytest.mark.skipif(
    not (API_KEY and FOREIGN_COMPANION_ID),
    reason="Provide TEST_EM_FOREIGN_COMPANION_ID to validate cross-project isolation.",
)
def test_cross_project_companion_is_hidden():
    url = f"{BASE_URL}/v1/companions/{FOREIGN_COMPANION_ID}"
    headers = _headers()

    with httpx.Client(timeout=10.0) as client:
        response = client.get(url, headers=headers)
        assert response.status_code == HTTPStatus.NOT_FOUND


def test_sync_chat_completion_live():
    chat_url = f"{BASE_URL}/v1/companions/{COMPANION_ID}/chat"
    headers = _headers()
    payload = {
        "external_user_id": "integration-user",
        "message": "Say hello to the test harness.",
        "model": "openai-gpt4o-mini",
    }
    with httpx.Client(timeout=30.0) as client:
        response = client.post(chat_url, headers=headers, json=payload)
        assert response.status_code == HTTPStatus.OK
        body = response.json()
        assert body["object"] == "chat.completion"
        assert body["choices"][0]["message"]["role"] == "assistant"


def test_stream_chat_completion_live():
    stream_url = f"{BASE_URL}/v1/companions/{COMPANION_ID}/chat/stream"
    headers = _headers()
    headers["Accept"] = "text/event-stream"
    payload = {
        "external_user_id": "integration-stream-user",
        "message": "Stream a quick greeting.",
        "model": "openai-gpt4o-mini",
    }

    events: list[str] = []
    deltas: list[str] = []

    with httpx.Client(timeout=None) as client:
        with client.stream("POST", stream_url, headers=headers, json=payload) as response:
            assert response.status_code == HTTPStatus.OK
            current_event = None
            for raw_line in response.iter_lines():
                if raw_line is None:
                    continue
                line = raw_line.decode() if isinstance(raw_line, bytes) else raw_line
                if not line:
                    continue
                if line.startswith("event:"):
                    current_event = line.split("event:")[1].strip()
                    events.append(current_event)
                elif line.startswith("data:") and current_event:
                    data = json.loads(line.split("data:")[1].strip())
                    if current_event == "delta":
                        content = data["choices"][0]["delta"].get("content", "")
                        if content:
                            deltas.append(content)
                if current_event == "done":
                    break

        assert "ack" in events
        assert "delta" in events
    assert deltas, "expected streaming deltas"
    assert "".join(deltas).strip() != ""

from __future__ import annotations

"""Label Conversations worker (Modal App)

Deploy this file with:
  modal deploy server/app/modals/workers/label_conversations.py
"""

import json
import os
from typing import Any, Dict, List, Optional

from modal import App, Cls, Image, Secret

app = App(name="em-analytics")
image = Image.debian_slim().pip_install("openai")


def _format_messages_for_llm(messages: List[Dict[str, Any]], max_chars: int = 20000) -> str:
    parts: List[str] = []
    for m in messages[-300:]:
        role = m.get("role", "")
        content = (m.get("content") or "").replace("\n\n", "\n").strip()
        parts.append(f"{role}: {content}")
    text = "\n".join(parts)
    if len(text) > max_chars:
        text = text[-max_chars:]
    return text


async def _classify_openrouter(messages_text: str) -> Dict[str, Any] | None:
    try:
        import openai

        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            return None
        client = openai.AsyncOpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
        sys_prompt = (
            "You are a careful classifier. Return ONLY valid JSON with exactly these keys: "
            '{"engagement_label":"not_engaged|engaged|very_engaged",'
            '"engagement_confidence":0.0,'
            '"dependency_risk_label":"no_risk|some_risk",'
            '"dependency_confidence":0.0}. '
            "No prose."
        )
        user_msg = (
            "Classify this conversation on engagement and dependency risk.\n\n" + messages_text
        )
        resp = await client.chat.completions.create(
            model="google/gemini-2.5-flash-lite",
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.0,
            max_tokens=200,
        )
        content = resp.choices[0].message.content if resp.choices else None
        return json.loads(content) if content else None
    except Exception:
        return None


async def _classify_openai(model: str, messages_text: str) -> Dict[str, Any] | None:
    try:
        import openai

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None
        client = openai.AsyncOpenAI(api_key=api_key)
        sys_prompt = (
            "You are a careful classifier. Return ONLY valid JSON with exactly these keys: "
            '{"engagement_label":"not_engaged|engaged|very_engaged",'
            '"engagement_confidence":0.0,'
            '"dependency_risk_label":"no_risk|some_risk",'
            '"dependency_confidence":0.0}. '
            "No prose."
        )
        user_msg = (
            "Classify this conversation on engagement and dependency risk.\n\n" + messages_text
        )
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.0,
            max_tokens=200,
        )
        content = resp.choices[0].message.content if resp.choices else None
        return json.loads(content) if content else None
    except Exception:
        return None


@app.function(
    image=image,
    secrets=[Secret.from_name("em-service-secrets")],
    timeout=60 * 30,
)
async def label_conversations(payload: Dict[str, Any]) -> None:
    """Label conversations for a companion.

    Expects payload: { job_id, companion_id, labels_version?, skip_existing?, since? }
    """
    job_id = payload.get("job_id")
    companion_id = payload.get("companion_id")
    labels_version = int(payload.get("labels_version", 1))
    skip_existing = bool(payload.get("skip_existing", True))
    since = payload.get("since")

    # Lookup DB gateway functions
    db_app = os.getenv("MODAL_DB_APP", "em-db")
    Gw = Cls.lookup(db_app, "DbGateway")

    try:
        targets: List[str] = Gw.start_labeling_job.remote(
            job_id, companion_id, skip_existing, since
        )

        processed = 0
        errors = 0

        BATCH_SIZE = 25
        for i in range(0, len(targets), BATCH_SIZE):
            batch_ids = targets[i : i + BATCH_SIZE]
            contexts = Gw.get_contexts.remote(batch_ids)
            items: List[Dict[str, Any]] = []
            for ctx in contexts:
                cid = ctx["conversation_id"]
                try:
                    text = ctx["text"]
                    result = await _classify_openrouter(text)
                    used_model = "google/gemini-2.5-flash-lite"
                    used_provider = "google"
                    if result is None:
                        for m in ("gpt-4.1-nano", "gpt-4.1-mini"):
                            result = await _classify_openai(m, text)
                            if result is not None:
                                used_model = m
                                used_provider = "openai"
                                break
                    if result is None:
                        raise RuntimeError("Classification failed")
                    items.append(
                        {
                            "conversation_id": cid,
                            "engagement_label": str(result.get("engagement_label", "engaged")),
                            "dependency_risk_label": str(
                                result.get("dependency_risk_label", "no_risk")
                            ),
                            "engagement_confidence": float(
                                result.get("engagement_confidence", 0.5)
                            ),
                            "dependency_confidence": float(
                                result.get("dependency_confidence", 0.5)
                            ),
                            "model": used_model,
                            "provider": used_provider,
                            "labels_version": labels_version,
                            "status": "COMPLETED",
                        }
                    )
                    processed += 1
                except Exception as e:
                    items.append(
                        {
                            "conversation_id": cid,
                            "engagement_label": "engaged",
                            "dependency_risk_label": "no_risk",
                            "engagement_confidence": None,
                            "dependency_confidence": None,
                            "model": "google/gemini-2.5-flash-lite",
                            "provider": "google",
                            "labels_version": labels_version,
                            "status": "FAILED",
                            "error": str(e),
                        }
                    )
                    errors += 1
            if items:
                Gw.upsert_labels_batch.remote(job_id, items)

        if job_id:
            Gw.mark_job_completed.remote(job_id, processed, len(targets), errors)
    except Exception as e:
        if job_id:
            try:
                Gw.mark_job_failed.remote(job_id, str(e))
            except Exception:
                pass
        raise

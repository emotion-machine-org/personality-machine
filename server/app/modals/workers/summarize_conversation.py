from __future__ import annotations

"""Conversation summarization worker (Modal App)

Deploy with:
  modal deploy server/app/modals/workers/summarize_conversation.py
"""

import os
from typing import Any, Dict, List

from modal import App, Cls, Image, Secret

app = App(name="em-summary")
image = Image.debian_slim().pip_install("openai")


async def _embed(text: str) -> List[float]:
    import openai

    client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    model = os.getenv("MEMORY_EMBEDDING_MODEL", "text-embedding-3-small")
    resp = await client.embeddings.create(model=model, input=text)
    return list(resp.data[0].embedding)  # type: ignore[attr-defined]


def _summary_system_prompt() -> str:
    return (
        "You are a helpful assistant that writes concise conversation summaries for an AI companion.\n"
        "Return bullet points capturing: user identity/name, stable preferences, goals and plans,\n"
        "decisions/actions taken, deadlines/dates, constraints, and unresolved questions.\n"
        "Focus on durable, memory-worthy facts. Keep to 8–15 bullets, each short."
    )


@app.function(
    image=image,
    secrets=[Secret.from_name("em-service-secrets")],
    timeout=60 * 10,
)
async def summarize_conversation(payload: Dict[str, Any]) -> None:
    import openai

    job_id: str = payload["job_id"]
    conversation_id: str = payload["conversation_id"]

    # Acquire contexts and meta from DbGateway
    db_app = os.getenv("MODAL_DB_APP", "em-db")
    Gw = Cls.lookup(db_app, "DbGateway")

    info = await Gw.start_summary_job.remote(job_id, conversation_id)
    companion_id = info["companion_id"]
    external_user_id = info.get("external_user_id")
    messages = info.get("messages", [])

    # Build linearized conversation text
    parts: List[str] = []
    for m in messages:
        role = (m.get("role") or "").strip()
        content = (m.get("content") or "").replace("\n\n", "\n").strip()
        if content:
            parts.append(f"{role}: {content}")
    convo_text = "\n".join(parts)[-40000:]  # keep last ~40k chars

    client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    model = os.getenv("SUMMARY_MODEL", os.getenv("MEMORY_IMPORTANCE_MODEL", "gpt-4o-mini"))
    sys_prompt = _summary_system_prompt()
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": f"Conversation transcript:\n{convo_text}"},
            ],
            temperature=0.2,
            max_tokens=512,
        )
        summary = (resp.choices[0].message.content or "").strip()
        if not summary:
            raise RuntimeError("empty summary")
    except Exception as e:
        await Gw.mark_job_failed.remote(job_id, f"summary LLM failed: {e}")
        return

    try:
        emb = await _embed(summary)
        await Gw.create_memories_batch.remote(
            [
                {
                    "companion_id": companion_id,
                    "content": summary,
                    "embedding": emb,
                    "importance": 0.7,
                    "weight_user": 1.0,
                    "modality": "summary",
                    "commentary": "conversation_summary",
                    "conversation_id": conversation_id,
                    "sender_type": "system",
                    "external_user_id": external_user_id,
                    "message_id": None,
                    "is_core": False,
                }
            ]
        )
        await Gw.mark_job_completed.remote(job_id, processed=1, total=1, errors=0)
    except Exception as e:
        await Gw.mark_job_failed.remote(job_id, f"summary write failed: {e}")

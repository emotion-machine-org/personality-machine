from __future__ import annotations

"""Privacy Redaction worker (Modal App)

Deploy with:
  modal deploy server/app/modals/workers/redact_conversation.py
"""

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from modal import App, Cls, Image, Secret

app = App(name="em-privacy")
image = Image.debian_slim().pip_install("openai")


def _regex_spans(text: str) -> List[Dict[str, Any]]:
    spans: List[Dict[str, Any]] = []
    # Simple, conservative patterns
    email_re = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
    phone_re = re.compile(r"(?:(?:\+\d{1,3}[\s-]?)?(?:\(\d{2,3}\)|\d{2,3})[\s-]?)?\d{3}[\s-]?\d{4}")
    url_re = re.compile(r"https?://[\w./?=&%#-]+", re.IGNORECASE)
    card_re = re.compile(r"(?:\d[ -]*?){13,16}")
    # naive US SSN
    ssn_re = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
    # simple name hints: "dear John", "my name is John", "I'm John", "I am John"
    dear_name_re = re.compile(r"(?i)\bdear\s+([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?)\b")
    my_name_is_re = re.compile(
        r"(?i)\b(?:my name is|i am|i'm|call me)\s+([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?)\b"
    )

    for label, pattern in (
        ("EMAIL", email_re),
        ("PHONE", phone_re),
        ("URL", url_re),
        ("CARD", card_re),
        ("SSN", ssn_re),
    ):
        for m in pattern.finditer(text or ""):
            start, end = m.span()
            if end > start:
                spans.append({"label": label, "start": start, "end": end})
    # Heuristic name spans (PERSON) from common patterns
    for pattern in (dear_name_re, my_name_is_re):
        for m in pattern.finditer(text or ""):
            name = m.group(1)
            if not name:
                continue
            start = m.start(1)
            end = m.end(1)
            if end > start:
                spans.append({"label": "PERSON", "start": start, "end": end})
    # Merge overlapping spans conservatively
    spans.sort(key=lambda s: (s["start"], s["end"]))
    merged: List[Dict[str, Any]] = []
    for s in spans:
        if not merged:
            merged.append(s)
        else:
            last = merged[-1]
            if s["start"] <= last["end"]:
                last["end"] = max(last["end"], s["end"])
            else:
                merged.append(s)
    return merged


def _find_spans_for_terms(text: str, terms: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deterministically locate all occurrences of LLM-identified PII terms.

    terms: list of { label: str, text: str }
    Returns list of { label, start, end } (0-based, inclusive-exclusive)
    """
    spans: List[Dict[str, Any]] = []
    if not text:
        return spans
    for t in terms or []:
        try:
            label = str(t.get("label", "PII"))
            needle = str(t.get("text", ""))
            if not needle:
                continue
            # Build a conservative regex: use word-boundaries for word-like needles
            # Detect word-like: letters, spaces, simple punctuation common in names/handles
            wordish = bool(re.fullmatch(r"[A-Za-z][A-Za-z .'-]*[A-Za-z]", needle))
            if wordish:
                pattern = r"(?<!\w)" + re.escape(needle) + r"(?!\w)"
            else:
                pattern = re.escape(needle)
            # First try exact case matches, then fallback to case-insensitive if no hits
            matches = list(re.finditer(pattern, text))
            if not matches:
                matches = list(re.finditer(pattern, text, flags=re.IGNORECASE))
            for m in matches:
                start, end = m.span()
                if end > start:
                    spans.append({"label": label, "start": start, "end": end})
        except Exception:
            continue
    # Merge overlaps
    spans.sort(key=lambda s: (s["start"], s["end"]))
    merged: List[Dict[str, Any]] = []
    for s in spans:
        if not merged:
            merged.append(s)
        else:
            last = merged[-1]
            if s["start"] <= last["end"]:
                last["end"] = max(last["end"], s["end"])
            else:
                merged.append(s)
    return merged


async def _llm_terms_openrouter(text: str) -> List[Dict[str, Any]] | None:
    """Ask OpenRouter (Gemini Flash Lite) to identify PII TERMS, not indices."""
    try:
        import openai

        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            return None
        client = openai.AsyncOpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
        sys_prompt = (
            "You identify PII TERMS in text, not indices. "
            'Return ONLY valid JSON as {"terms":[{"label":"TYPE","text":"EXACT SUBSTRING"}, ...]}. '
            'The "text" field must be a verbatim substring from the input. '
            "PII types include: personal names (PERSON), emails (EMAIL), phone numbers (PHONE), "
            "addresses (ADDRESS), birthdays (DOB), government IDs (GOV_ID), SSN, credit cards (CARD), "
            "bank accounts (BANK), precise geolocation (GEO), URLs that identify a person (URL), usernames/handles (HANDLE). "
            "No explanations or additional text."
        )
        user_msg = "Identify PII TERMS in this text.\n\n" + (text or "")
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
        data = None
        if content:
            try:
                data = json.loads(content)
            except Exception:
                start = content.find("{")
                end = content.rfind("}")
                if start != -1 and end != -1 and end > start:
                    try:
                        data = json.loads(content[start : end + 1])
                    except Exception:
                        data = None
        terms = data.get("terms") if isinstance(data, dict) else None
        if isinstance(terms, list):
            out: List[Dict[str, Any]] = []
            for t in terms:
                if isinstance(t, dict) and t.get("text"):
                    out.append({"label": str(t.get("label", "PII")), "text": str(t.get("text"))})
            return out
        return None
    except Exception:
        return None


async def _llm_terms_openai(model: str, text: str) -> List[Dict[str, Any]] | None:
    """Ask OpenAI to identify PII TERMS, not indices."""
    try:
        import openai

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None
        client = openai.AsyncOpenAI(api_key=api_key)
        sys_prompt = (
            "You identify PII TERMS in text, not indices. "
            'Return ONLY valid JSON as {"terms":[{"label":"TYPE","text":"EXACT SUBSTRING"}, ...]}. '
            'The "text" field must be a verbatim substring from the input. '
            "PII types include: personal names (PERSON), emails (EMAIL), phone numbers (PHONE), "
            "full street/postal addresses (ADDRESS), exact birthdays (DOB), government IDs (GOV_ID), SSN, "
            "credit cards (CARD), bank accounts (BANK), precise geolocation (GEO), URLs that identify a person (URL), and usernames/handles (HANDLE). "
            "Do not include non-private text. No prose."
        )
        user_msg = "Identify PII TERMS in this text.\n\n" + (text or "")
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
        data = None
        if content:
            try:
                data = json.loads(content)
            except Exception:
                start = content.find("{")
                end = content.rfind("}")
                if start != -1 and end != -1 and end > start:
                    try:
                        data = json.loads(content[start : end + 1])
                    except Exception:
                        data = None
        terms = data.get("terms") if isinstance(data, dict) else None
        if isinstance(terms, list):
            out: List[Dict[str, Any]] = []
            for t in terms:
                if isinstance(t, dict) and t.get("text"):
                    out.append({"label": str(t.get("label", "PII")), "text": str(t.get("text"))})
            return out
        return None
    except Exception:
        return None


async def _llm_spans_openrouter(text: str) -> List[Dict[str, Any]] | None:
    try:
        import openai

        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            return None
        client = openai.AsyncOpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
        sys_prompt = (
            "You extract PII spans in text. Use 0-based character indices. "
            'Return ONLY valid JSON as {"spans":[{"label":"TYPE","start":0,"end":1}, ...]}. '
            "PII types include: personal names (first/last/nickname, label PERSON), emails (EMAIL), phone numbers (PHONE), "
            "full street/postal addresses (ADDRESS), exact birthdays (DOB), government IDs (GOV_ID), social security numbers (SSN), "
            "credit cards (CARD), bank accounts (BANK), precise geolocation (GEO), URLs that identify a person (URL), and usernames/handles (HANDLE). "
            "Do not include non-private text. No prose."
        )
        user_msg = "Find PII spans in this text. Use 0-based character indices.\n\n" + (text or "")
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
        data = None
        if content:
            try:
                data = json.loads(content)
            except Exception:
                # Try to extract JSON substring
                start = content.find("{")
                end = content.rfind("}")
                if start != -1 and end != -1 and end > start:
                    try:
                        data = json.loads(content[start : end + 1])
                    except Exception:
                        data = None
        spans = data.get("spans") if isinstance(data, dict) else None
        if isinstance(spans, list):
            return spans
        return None
    except Exception:
        return None


async def _llm_spans_openai(model: str, text: str) -> List[Dict[str, Any]] | None:
    try:
        import openai

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None
        client = openai.AsyncOpenAI(api_key=api_key)
        sys_prompt = (
            "You extract PII spans in text. Use 0-based character indices. "
            'Return ONLY valid JSON as {"spans":[{"label":"TYPE","start":0,"end":1}, ...]}. '
            "PII types include: personal names (PERSON), emails (EMAIL), phone numbers (PHONE), "
            "full street/postal addresses (ADDRESS), exact birthdays (DOB), government IDs (GOV_ID), SSN, "
            "credit cards (CARD), bank accounts (BANK), precise geolocation (GEO), URLs that identify a person (URL), and usernames/handles (HANDLE). "
            "No prose."
        )
        user_msg = "Find PII spans in this text. Use 0-based character indices.\n\n" + (text or "")
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
        data = None
        if content:
            try:
                data = json.loads(content)
            except Exception:
                start = content.find("{")
                end = content.rfind("}")
                if start != -1 and end != -1 and end > start:
                    try:
                        data = json.loads(content[start : end + 1])
                    except Exception:
                        data = None
        spans = data.get("spans") if isinstance(data, dict) else None
        if isinstance(spans, list):
            return spans
        return None
    except Exception:
        return None


@app.function(
    image=image,
    secrets=[Secret.from_name("em-service-secrets")],
    timeout=60 * 20,
)
async def redact_conversation(payload: Dict[str, Any]) -> None:
    """Compute PII spans per message and persist to messages.pii_spans.

    Expects payload: { job_id, conversation_id, model?, provider? }
    """
    logger = logging.getLogger(__name__)

    job_id = payload.get("job_id")
    conversation_id = payload.get("conversation_id")
    model = payload.get("model")
    provider = payload.get("provider")

    db_app = os.getenv("MODAL_DB_APP", "em-db")
    Gw = Cls.lookup(db_app, "DbGateway")

    try:
        # Mark RUNNING and fetch messages
        msgs: List[Dict[str, Any]] = Gw.start_privacy_job.remote(job_id, conversation_id)
        processed = 0
        errors = 0

        BATCH = 50
        batch_items: List[Dict[str, Any]] = []
        for m in msgs:
            try:
                content = m.get("content") or ""
                # 1) Ask LLM to identify PII TERMS (not indices)
                terms: List[Dict[str, Any]] | None = await _llm_terms_openrouter(content)
                if not terms:
                    for mname in ("gpt-4.1-nano", "gpt-4.1-mini"):
                        terms = await _llm_terms_openai(mname, content)
                        if terms is not None:
                            break
                # 2) Deterministically locate spans for those terms
                term_spans: List[Dict[str, Any]] = _find_spans_for_terms(content, terms or [])
                # 3) Add regex fallback spans for common PII if needed
                regex_spans = _regex_spans(content)
                # Merge and dedupe
                norm: List[Dict[str, Any]] = term_spans + regex_spans
                norm.sort(key=lambda x: (x["start"], x["end"]))
                merged: List[Dict[str, Any]] = []
                for s in norm:
                    if merged and s["start"] <= merged[-1]["end"]:
                        merged[-1]["end"] = max(merged[-1]["end"], s["end"])
                    else:
                        merged.append(s)

                batch_items.append(
                    {
                        "message_id": m["message_id"],
                        "pii_spans": merged,
                    }
                )
                processed += 1
            except Exception:
                errors += 1
            if len(batch_items) >= BATCH:
                Gw.set_message_redactions_batch.remote(job_id, batch_items)
                batch_items = []

        if batch_items:
            Gw.set_message_redactions_batch.remote(job_id, batch_items)

        Gw.mark_job_completed.remote(job_id, processed, len(msgs), errors)
        # Update conversation marker for last computed timestamp
        Gw.set_conversation_privacy_computed.remote(conversation_id)
    except Exception as e:
        if job_id:
            try:
                Gw.mark_job_failed.remote(job_id, str(e))
            except Exception:
                pass
        raise

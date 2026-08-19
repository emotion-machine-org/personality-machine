from __future__ import annotations

"""Lazy singletons for OpenAI-compatible async clients.

Avoids per-request client creation and enables HTTP keep-alive reuse.
"""

import os
from functools import cache


@cache
def get_openai_async_client():
    import openai

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    timeout_s = float(os.getenv("OPENAI_TIMEOUT_S", os.getenv("TEXT_OPENAI_TIMEOUT_S", "45")))
    max_retries = int(os.getenv("OPENAI_MAX_RETRIES", "1"))
    return openai.AsyncOpenAI(api_key=api_key, timeout=timeout_s, max_retries=max_retries)


@cache
def get_openrouter_async_client():
    import openai

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set for OpenRouter client")
    base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    timeout_s = float(os.getenv("OPENAI_TIMEOUT_S", os.getenv("TEXT_OPENAI_TIMEOUT_S", "45")))
    max_retries = int(os.getenv("OPENAI_MAX_RETRIES", "1"))
    return openai.AsyncOpenAI(
        api_key=api_key, base_url=base_url, timeout=timeout_s, max_retries=max_retries
    )


@cache
def get_vllm_async_client():
    import openai

    api_key = os.getenv("VLLM_API_KEY")
    base_url = os.getenv("VLLM_URL")
    if not base_url:
        raise RuntimeError("VLLM_URL is not set")
    timeout_s = float(os.getenv("OPENAI_TIMEOUT_S", os.getenv("TEXT_OPENAI_TIMEOUT_S", "45")))
    max_retries = int(os.getenv("OPENAI_MAX_RETRIES", "0"))
    return openai.AsyncOpenAI(
        api_key=api_key, base_url=base_url, timeout=timeout_s, max_retries=max_retries
    )

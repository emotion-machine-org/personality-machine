"""Service for extracting descriptions from images using a vision model."""

from __future__ import annotations

import base64
import logging
import os
import time

from dotenv import load_dotenv

from .openai_clients import get_openrouter_async_client

load_dotenv()

logger = logging.getLogger(__name__)

# Configuration
DEFAULT_VISION_MODEL = os.getenv("IMAGE_DESCRIPTION_MODEL", "google/gemini-2.0-flash-001")
DEFAULT_MAX_TOKENS = int(os.getenv("IMAGE_DESCRIPTION_MAX_TOKENS", "500"))

# System prompt for image description
IMAGE_DESCRIPTION_SYSTEM_PROMPT = """You are an image analysis assistant. Describe the image in detail for a conversational AI that cannot see images. Include:
- Main subject and composition
- Any text visible in the image
- Colors, mood, setting
- Notable details that might be relevant for discussion

Be concise but thorough. The description will be used to help an AI companion respond to questions about this image."""


async def extract_image_description(
    image_url: str | None = None,
    image_base64: str | None = None,
    mime_type: str = "image/jpeg",
    user_context: str | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
) -> tuple[str, dict]:
    """
    Extract a detailed description from an image using a vision model.

    Args:
        image_url: URL to the image (presigned S3 URL or public URL)
        image_base64: Base64-encoded image data (alternative to URL)
        mime_type: MIME type of the image (for base64)
        user_context: Optional user message for context about what to look for
        model: Vision model to use (defaults to Gemini 2.0 Flash via OpenRouter)
        max_tokens: Maximum tokens for response

    Returns:
        Tuple of (description string, timing dict)

    Raises:
        ValueError: If neither image_url nor image_base64 is provided
        Exception: On API errors
    """
    if not image_url and not image_base64:
        raise ValueError("Either image_url or image_base64 must be provided")

    timings: dict = {}
    t0 = time.perf_counter()

    # Build the image content for the vision model
    if image_url:
        image_content = {
            "type": "image_url",
            "image_url": {"url": image_url},
        }
    else:
        # Base64 encoded image
        image_content = {
            "type": "image_url",
            "image_url": {"url": f"data:{mime_type};base64,{image_base64}"},
        }

    # Build the user message
    user_text = "Describe this image in detail."
    if user_context:
        user_text = f'The user sent this message with the image: "{user_context}"\n\nDescribe the image, keeping the user\'s message in mind.'

    messages = [
        {"role": "system", "content": IMAGE_DESCRIPTION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                image_content,
                {"type": "text", "text": user_text},
            ],
        },
    ]

    # Get the OpenRouter client for Gemini
    client = get_openrouter_async_client()
    vision_model = model or DEFAULT_VISION_MODEL
    tokens = max_tokens or DEFAULT_MAX_TOKENS

    try:
        t_api = time.perf_counter()
        response = await client.chat.completions.create(
            model=vision_model,
            messages=messages,
            max_tokens=tokens,
            temperature=0.3,  # Lower temperature for factual descriptions
        )
        timings["api_ms"] = (time.perf_counter() - t_api) * 1000

        description = response.choices[0].message.content or ""
        description = description.strip()

        # Log usage if available
        if response.usage:
            timings["prompt_tokens"] = response.usage.prompt_tokens
            timings["completion_tokens"] = response.usage.completion_tokens

        logger.info(
            f"[IMAGE_DESCRIPTION] Extracted description ({len(description)} chars) "
            f"using {vision_model} in {timings.get('api_ms', 0):.0f}ms"
        )

    except Exception as e:
        logger.error(f"[IMAGE_DESCRIPTION] Failed to extract description: {e}")
        raise

    timings["total_ms"] = (time.perf_counter() - t0) * 1000
    timings["model"] = vision_model

    return description, timings


async def extract_description_from_bytes(
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
    user_context: str | None = None,
    model: str | None = None,
) -> tuple[str, dict]:
    """
    Extract description from raw image bytes.

    Args:
        image_bytes: Raw image data
        mime_type: MIME type of the image
        user_context: Optional context about what to look for
        model: Vision model to use

    Returns:
        Tuple of (description string, timing dict)
    """
    image_base64 = base64.b64encode(image_bytes).decode("utf-8")
    return await extract_image_description(
        image_base64=image_base64,
        mime_type=mime_type,
        user_context=user_context,
        model=model,
    )


def build_image_context_block(
    image_descriptions: list[dict],
) -> str:
    """
    Build a context block for images in the current turn.

    Args:
        image_descriptions: List of dicts with 'description' and optionally 'uploaded_at'

    Returns:
        Formatted context block string for system prompt injection
    """
    if not image_descriptions:
        return ""

    if len(image_descriptions) == 1:
        desc = image_descriptions[0].get("description", "")
        return f"""## Current Image Context

The user has shared an image. Here's what it contains:

{desc}

Respond naturally to the user's message, incorporating your understanding of this image when relevant."""

    # Multiple images
    lines = ["## Current Image Context", "", "The user has shared multiple images:", ""]
    for i, img in enumerate(image_descriptions, 1):
        desc = img.get("description", "")
        lines.append(f"**Image {i}:**")
        lines.append(desc)
        lines.append("")

    lines.append(
        "Respond naturally to the user's message, incorporating your understanding of these images when relevant."
    )
    return "\n".join(lines)

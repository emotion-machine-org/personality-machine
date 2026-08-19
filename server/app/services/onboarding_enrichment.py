"""
Onboarding enrichment service.

Transforms conversational onboarding input into a full CompanionConfig.
Optimized for open-ended conversation where the description captures user intent.
"""

from __future__ import annotations

import logging
import random
from typing import Literal

from pydantic import BaseModel, Field

from ..models.companion import (
    CompanionConfig,
    InferenceConfig,
    MemoryConfig,
    SystemPrompt,
    VoiceConfig,
)

logger = logging.getLogger(__name__)


Vibe = Literal["chill", "energetic", "warm", "witty", "intense"]


class OnboardingInput(BaseModel):
    """Input from conversational onboarding."""

    description: str = Field(..., description="Rich description of the companion from conversation")
    name: str | None = Field(default=None, description="Optional companion name")
    vibe: Vibe | None = Field(default=None, description="Optional overall vibe")


# Vibe modifiers - short additions based on vibe
VIBE_MODIFIERS = {
    "chill": "You have a laid-back, easy-going energy. No rush, no pressure.",
    "energetic": "You bring enthusiasm and positive energy to conversations.",
    "warm": "You're genuinely caring and make people feel comfortable and valued.",
    "witty": "You're quick with clever observations and enjoy playful banter.",
    "intense": "You're deeply engaged and passionate about meaningful conversation.",
}

# Names by vibe (fallback when no name provided)
VIBE_NAMES = {
    "chill": ["Sage", "River", "Mellow", "Drift", "Breeze"],
    "energetic": ["Spark", "Blaze", "Nova", "Flash", "Bolt"],
    "warm": ["Luna", "Ember", "Sunny", "Hearth", "Glow"],
    "witty": ["Quip", "Jest", "Puck", "Zephyr", "Dash"],
    "intense": ["Phoenix", "Storm", "Atlas", "Apex", "Forge"],
}

DEFAULT_NAMES = ["Aura", "Echo", "Pixel", "Orion", "Muse", "Kai", "Nova", "Zen"]


def generate_companion_name(vibe: Vibe | None = None) -> str:
    """Generate a random companion name, optionally based on vibe."""
    if vibe and vibe in VIBE_NAMES:
        return random.choice(VIBE_NAMES[vibe])
    return random.choice(DEFAULT_NAMES)


def generate_system_prompt(input: OnboardingInput) -> str:
    """Generate a system prompt from onboarding input."""

    # Build the core identity from description
    prompt_parts = [f"You are: {input.description}"]

    # Add vibe modifier if provided
    if input.vibe and input.vibe in VIBE_MODIFIERS:
        prompt_parts.append(VIBE_MODIFIERS[input.vibe])

    # Add engagement guidelines
    prompt_parts.append("""
## How to Engage
- Be present and genuinely interested in what the user shares
- Build on previous conversations - reference things they've mentioned
- Match their energy - excited with them, calm when they need grounding
- Ask follow-up questions that show you're paying attention
- Share your perspective - you're a companion, not just a helper

## Keep in Mind
- Stay in character naturally - embody who you are without announcing it
- Keep responses concise unless depth is needed
- Be real - it's okay to not know things or disagree respectfully
- You're building a relationship, not just answering questions""")

    return "\n\n".join(prompt_parts)


def enrich_onboarding_input(input: OnboardingInput) -> tuple[CompanionConfig, str]:
    """Transform onboarding input into a full CompanionConfig.

    Args:
        input: The input from conversational onboarding.

    Returns:
        A tuple of (CompanionConfig, companion_name).
    """
    full_prompt = generate_system_prompt(input)

    system_prompt = SystemPrompt(
        full_system_prompt=full_prompt,
    )

    inference = InferenceConfig(
        model="openai-gpt4o-mini",
        temperature=0.7,
    )

    voice = VoiceConfig(
        preset="gpt4o-mini-elevenlabs",
        voice_name="Sarah",
    )

    memory = MemoryConfig(
        enabled=True,
        version=2,
    )

    config = CompanionConfig(
        system_prompt=system_prompt,
        inference=inference,
        voice=voice,
        memory=memory,
        context_mode="layered",
    )

    name = input.name if input.name else generate_companion_name(input.vibe)

    logger.info(
        "Enriched onboarding input",
        extra={
            "has_vibe": bool(input.vibe),
            "vibe": input.vibe,
            "generated_name": name,
            "description_length": len(input.description),
        },
    )

    return config, name


# Keep old function for backward compatibility during transition
def enrich_onboarding_answers(answers) -> tuple[CompanionConfig, str]:
    """Legacy function - converts old format to new format."""
    # Map old format to new
    description = (
        answers.custom_purpose
        if answers.custom_purpose
        else f"A {answers.approach} {answers.purpose} with a {answers.tone} communication style"
    )

    # Map old approach to vibe
    approach_to_vibe = {
        "playful": "witty",
        "supportive": "warm",
        "challenging": "intense",
    }
    vibe = approach_to_vibe.get(answers.approach)

    input = OnboardingInput(
        description=description,
        name=answers.name,
        vibe=vibe,
    )

    return enrich_onboarding_input(input)

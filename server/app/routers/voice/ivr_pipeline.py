# server/app/routers/voice/ivr_pipeline.py
"""IVR (Interactive Voice Response) navigation pipeline builder.

This module provides utilities for building voice pipelines that can navigate
through automated phone menus (IVR systems) using pipecat's IVRNavigator.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.extensions.ivr.ivr_navigator import IVRNavigator, IVRStatus

from .services import build_llm_service

if TYPE_CHECKING:
    from .providers import LLMProvider

logger = logging.getLogger(__name__)

# Default goal when none is specified - aims to reach a human agent
DEFAULT_IVR_GOAL = (
    "Navigate through any automated phone menus to reach a human representative. "
    "If you're asked to select options, choose the most relevant one to speak with a person. "
    "If asked for account information you don't have, try to reach an operator or say you'd like to speak with someone."
)

# VAD parameters optimized for IVR systems
# Longer stop_secs to allow complete menu announcements before responding
IVR_VAD_PARAMS = VADParams(stop_secs=2.0)

# VAD parameters for human conversation after IVR navigation
CONVERSATION_VAD_PARAMS = VADParams(stop_secs=0.8)


def build_ivr_navigator(
    llm_provider: LLMProvider,
    ivr_goal: str | None = None,
    temperature: float = 0.3,
) -> IVRNavigator:
    """Build an IVR navigator with the specified goal.

    The IVRNavigator uses an LLM to understand IVR menu options and make
    navigation decisions. It can send DTMF tones and speak responses.

    Args:
        llm_provider: LLM provider to use for navigation decisions
        ivr_goal: Navigation goal describing what to accomplish.
                  If None, defaults to reaching a human agent.
        temperature: LLM temperature (lower = more deterministic, good for IVR)

    Returns:
        Configured IVRNavigator instance
    """
    goal = ivr_goal or DEFAULT_IVR_GOAL

    # Build LLM service for the navigator
    # Use a minimal system prompt - IVRNavigator has its own prompting
    llm = build_llm_service(
        provider=llm_provider,
        system_prompt="You are navigating an automated phone system.",
        temperature=temperature,
    )

    logger.info(f"[IVR] Building IVRNavigator with goal: {goal[:100]}...")

    return IVRNavigator(
        llm=llm,
        ivr_prompt=goal,
        ivr_vad_params=IVR_VAD_PARAMS,
    )


__all__ = [
    "CONVERSATION_VAD_PARAMS",
    "DEFAULT_IVR_GOAL",
    "IVR_VAD_PARAMS",
    "IVRNavigator",
    "IVRStatus",
    "build_ivr_navigator",
]

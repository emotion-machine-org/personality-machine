"""Schemas for the Intent Classifier.

The intent classifier is an LLM-based router that decides which layers and behaviors
to run based on user message and context. These schemas define the input/output
contracts for the classifier.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List

from pydantic import BaseModel, Field


class ClassifierModel(str, Enum):
    """Available models for the intent classifier."""

    GEMINI_2_0_FLASH = "gemini-2.0-flash"
    GPT_4O_MINI = "gpt-4o-mini"
    GPT_5_NANO = "gpt-5-nano"
    GPT_5_MINI = "gpt-5-mini"


class ClassifierConfig(BaseModel):
    """Configuration for the intent classifier.

    Stored in companion_versions.config JSON under "classifier" key.
    """

    enabled: bool = True
    model: ClassifierModel = ClassifierModel.GEMINI_2_0_FLASH
    custom_model_id: str | None = None  # For CUSTOM model
    timeout_ms: int = 10000
    history_limit: int = 20  # Last N messages to include
    instructions: str | None = None  # Custom instructions for when to enable layers


class LayerInfo(BaseModel):
    """Information about a named layer for the classifier."""

    name: str
    category: str  # "memory", "knowledge_base", "tools"
    description: str
    always_run: bool = False


class BehaviorInfo(BaseModel):
    """Information about a classifier-eligible behavior."""

    key: str
    name: str
    classifier_hint: str = ""  # Description for classifier to use (optional)


class ClassifierInput(BaseModel):
    """Input to the intent classifier."""

    user_message: str
    history: List[Dict[str, str]] = Field(default_factory=list)  # Last N messages
    turn_count: int = 0

    # State snapshots for classifier reasoning
    profile: Dict[str, Any] = Field(default_factory=dict)

    # Available options
    available_layers: List[LayerInfo] = Field(default_factory=list)
    available_behaviors: List[BehaviorInfo] = Field(default_factory=list)
    tool_summaries: List[Dict[str, str]] = Field(default_factory=list)  # [{spec_name, summary}]


class LayerDecision(BaseModel):
    """Classifier's decision for a single layer."""

    run: bool


class ClassifierOutput(BaseModel):
    """Output from the intent classifier.

    This is the structured response the classifier LLM produces.
    """

    layers: Dict[str, LayerDecision] = Field(default_factory=dict)
    behaviors: List[str] = Field(default_factory=list)  # Behavior keys to run


class ClassifierResult(BaseModel):
    """Full result from running the classifier, including metadata."""

    output: ClassifierOutput | None = None
    success: bool = True
    error: str | None = None
    duration_ms: float = 0.0
    model_used: str = ""
    fallback_to_raw: bool = False  # True if classifier failed and we're in raw mode


# Layer descriptions for the classifier prompt (kept short for speed)
LAYER_DESCRIPTIONS: Dict[str, str] = {
    "memory": "Past conversations and personal context",
    "knowledge_base": "Documentation, FAQs, knowledge sources",
    "tools": "External APIs, real-time data, scheduling",
}


__all__ = [
    "LAYER_DESCRIPTIONS",
    "BehaviorInfo",
    "ClassifierConfig",
    "ClassifierInput",
    "ClassifierModel",
    "ClassifierOutput",
    "ClassifierResult",
    "LayerDecision",
    "LayerInfo",
]

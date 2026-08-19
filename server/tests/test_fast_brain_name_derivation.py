from app.routers.client_api import (
    _derive_companion_name,
    _extract_name_from_markdown,
    _sanitize_companion_name,
)


def test_sanitize_companion_name_rejects_filename_style_identity() -> None:
    assert _sanitize_companion_name("IDENTITY.md - Who am I?") is None


def test_extract_name_from_markdown_picks_explicit_name_field() -> None:
    soul = """
# SOUL
name: Joseph
Tone: calm
""".strip()
    assert _extract_name_from_markdown(soul) == "Joseph"


def test_derive_companion_name_prefers_soul_over_identity_heading() -> None:
    soul = """
# SOUL
Companion Name: Joseph
""".strip()
    identity = """
# IDENTITY.md - Who am I?
This document contains operational notes.
""".strip()
    assert _derive_companion_name(soul, identity) == "Joseph"

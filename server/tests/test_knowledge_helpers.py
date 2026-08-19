import os
import sys
from pathlib import Path

# Ensure `app` package is importable when running this file directly.
SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from openai import NOT_GIVEN

from app.services import openai_vector_store
from app.services.knowledge_service import _payload_type_to_file
from app.services.openai_vector_store import _ranking_options


def test_payload_type_to_file_mappings():
    assert _payload_type_to_file("text") == ("txt", "text/plain")
    assert _payload_type_to_file("markdown") == ("md", "text/markdown")
    assert _payload_type_to_file("json") == ("json", "application/json")
    # default fallback
    assert _payload_type_to_file("unknown") == ("txt", "text/plain")


def test_ranking_options_defaults_and_overrides(monkeypatch):
    opts = _ranking_options()
    assert opts["ranker"] == "default-2024-11-15"
    assert opts["score_threshold"] == 0.2
    assert opts["hybrid_search"] == {"embedding_weight": 0.7, "text_weight": 0.3}

    # Defaults are read at import time; override the module constants.
    monkeypatch.setattr(openai_vector_store, "_DEFAULT_RANKER", "auto")
    monkeypatch.setattr(openai_vector_store, "_DEFAULT_SCORE_THRESHOLD", 0.8)
    opts = _ranking_options()
    assert opts["ranker"] == "auto"
    assert opts["score_threshold"] == 0.8
    assert opts["hybrid_search"] == {"embedding_weight": 0.7, "text_weight": 0.3}


def test_ranking_options_modes(monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_HYBRID_EMBED_WEIGHT", raising=False)
    monkeypatch.delenv("KNOWLEDGE_HYBRID_TEXT_WEIGHT", raising=False)

    opts_semantic = _ranking_options(mode="semantic")
    assert opts_semantic["hybrid_search"] == {"embedding_weight": 1.0}

    opts_keyword = _ranking_options(mode="keyword")
    assert opts_keyword["hybrid_search"] == {"text_weight": 1.0}

    opts_default = _ranking_options(mode="unknown")
    assert opts_default["hybrid_search"] == {"embedding_weight": 0.7, "text_weight": 0.3}

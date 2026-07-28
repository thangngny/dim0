"""Unit tests for the auto-model complexity classifier."""

import pytest

from topix.agents.assistant import auto_model
from topix.config.catalog import Resolved

PROVIDER_KEYS = [
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "MISTRAL_API_KEY",
    "DEEPSEEK_API_KEY",
]


@pytest.fixture
def clean_keys(monkeypatch):
    """Start from no provider keys so each test sets only what it needs."""
    for key in PROVIDER_KEYS:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def _fake_response(content: str):
    """Build a minimal object shaped like a litellm/OpenAI chat completion."""
    message = type("M", (), {"content": content})()
    choice = type("C", (), {"message": message})()
    return type("R", (), {"choices": [choice]})()


def test_parse_complexity_handles_valid_and_junk():
    """JSON with a valid label parses; anything else falls back to medium."""
    assert auto_model._parse_complexity('{"complexity": "complex"}') == "complex"
    assert auto_model._parse_complexity('{"complexity": "nonsense"}') == "medium"
    assert auto_model._parse_complexity("not json") == "medium"
    assert auto_model._parse_complexity(None) == "medium"


@pytest.mark.asyncio
async def test_classifier_works_for_native_non_openai_provider(clean_keys, monkeypatch):
    """The classifier routes via LiteLLM, so a native-only key still escalates.

    Regression: the previous OpenAI-client classifier returned None (always
    'medium') for providers like Anthropic that are not OpenAI-compatible.
    """
    clean_keys.setenv("ANTHROPIC_API_KEY", "an-x")

    # Isolate from local env config: keyless local providers (e.g. Ollama)
    # otherwise win the catalog's "best available lite" pick, so pin the
    # resolved model to a native Anthropic route regardless of environment.
    anthropic_lite = Resolved(
        id="anthropic/claude-haiku", label="Haiku", family="claude",
        tier="lite", dim=None, provider="anthropic",
        model="claude-haiku", call="anthropic/claude-haiku",
    )
    monkeypatch.setattr(
        auto_model.catalog, "default_resolved", lambda tier=None: anthropic_lite
    )

    captured = {}

    async def fake_acompletion(*, model, **kwargs):
        captured["model"] = model
        return _fake_response('{"complexity": "complex"}')

    monkeypatch.setattr(auto_model.litellm, "acompletion", fake_acompletion)

    result = await auto_model.classify_auto_model_complexity(
        [{"role": "user", "content": "Build a multi-step interactive visualizer"}]
    )

    assert result == "complex"
    # Routed through a native Anthropic model code, not an OpenAI-compatible one.
    assert captured["model"].startswith("anthropic/")


@pytest.mark.asyncio
async def test_classifier_falls_back_to_medium_without_models(clean_keys):
    """With no provider key, classification degrades to medium (no crash)."""
    result = await auto_model.classify_auto_model_complexity(
        [{"role": "user", "content": "hello"}]
    )
    assert result == "medium"

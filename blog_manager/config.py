"""Configuration for Blog Manager."""
from __future__ import annotations

import os
from pathlib import Path

from agents import OpenAIChatCompletionsModel, set_tracing_disabled
from agents.model_settings import ModelSettings, Reasoning
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

# Disable OpenAI tracing — we use OpenRouter, not the OpenAI tracing backend
set_tracing_disabled(True)

# Blog directories
_blog_dir_env = os.getenv("BLOG_DIR")
if not _blog_dir_env:
    raise ValueError("BLOG_DIR environment variable is required (path to your Jekyll blog root)")
BLOG_DIR = Path(_blog_dir_env).expanduser().resolve()
POSTS_DIR = BLOG_DIR / "_posts"
ASSETS_IMG_DIR = BLOG_DIR / "assets" / "img" / "blog"

# ---------------------------------------------------------------------------
# Model configuration
# ---------------------------------------------------------------------------

#: Default model for all agents (overridable per-agent below)
DEFAULT_MODEL = os.getenv("OPENROUTER_MODEL", "anthropic/claude-3.5-sonnet")

#: Per-agent model overrides — each falls back to DEFAULT_MODEL
ORCHESTRATOR_MODEL = os.getenv("ORCHESTRATOR_MODEL", DEFAULT_MODEL)
RESEARCHER_MODEL = os.getenv("RESEARCHER_MODEL", DEFAULT_MODEL)
WRITER_MODEL = os.getenv("WRITER_MODEL", DEFAULT_MODEL)
CRITIC_MODEL = os.getenv("CRITIC_MODEL", DEFAULT_MODEL)

#: Optional reasoning effort applied to agents whose model supports it.
#: Valid values: "low" | "medium" | "high"  (unset = reasoning disabled)
REASONING_EFFORT: str | None = os.getenv("OPENROUTER_REASONING_EFFORT") or None

# Model-name prefixes known to honour the reasoning/thinking parameter on OpenRouter.
# This list is checked before adding the reasoning config — models NOT in this list
# silently skip reasoning even if OPENROUTER_REASONING_EFFORT is set.
_REASONING_PREFIXES: tuple[str, ...] = (
    "openai/o1",
    "openai/o3",
    "openai/o4",
    "deepseek/deepseek-r",          # r1, r1-distill-*, etc.
    "anthropic/claude-3.7",          # claude-3.7-sonnet with extended thinking
    "anthropic/claude-opus-4",       # future opus with thinking
    "google/gemini-2.0-flash-think", # flash-thinking variants
    "google/gemini-2.5",             # gemini 2.5 reasoning models
    "qwen/qwq",
    "qwen/qwen3",                    # qwen3 series supports thinking mode
    "x-ai/grok-3-mini",
    "meta-llama/llama-4-maverick",
    "meta-llama/llama-4-scout",
)


def _supports_reasoning(model: str) -> bool:
    """Return True if *model* is known to support the reasoning effort parameter."""
    m = model.lower()
    return any(m.startswith(p) for p in _REASONING_PREFIXES)


def get_openrouter_client() -> AsyncOpenAI:
    """Create an AsyncOpenAI client pointing to OpenRouter."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY environment variable is required")
    return AsyncOpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        default_headers={
            "HTTP-Referer": "https://levulinh.github.io",
            "X-Title": "Blog Manager",
        },
    )


def get_model(model_name: str | None = None) -> OpenAIChatCompletionsModel:
    """Get an OpenAIChatCompletionsModel via OpenRouter."""
    return OpenAIChatCompletionsModel(
        model=model_name or DEFAULT_MODEL,
        openai_client=get_openrouter_client(),
    )


def get_model_settings(model_name: str | None = None) -> ModelSettings:
    """Return ModelSettings with reasoning configured when applicable.

    If OPENROUTER_REASONING_EFFORT is set and the chosen model is known to
    support the reasoning/thinking parameter on OpenRouter, the effort level
    is injected via ModelSettings.reasoning.  Otherwise an empty (default)
    ModelSettings is returned so every agent always receives one.
    """
    model = model_name or DEFAULT_MODEL
    if REASONING_EFFORT and _supports_reasoning(model):
        return ModelSettings(reasoning=Reasoning(effort=REASONING_EFFORT))
    return ModelSettings()

"""AI illustration generation via OpenRouter (Gemini image generation)."""
from __future__ import annotations

import base64
import json
import os
import tempfile
import uuid
from pathlib import Path

from agents import function_tool
from openai import OpenAI

_DEFAULT_IMAGE_MODEL = "google/gemini-3.1-flash-image-preview"

# Aspect ratio hint appended to the prompt so the model can compose accordingly
_ASPECT_HINT: dict[str, str] = {
    "16:9": "wide landscape (16:9)",
    "9:16": "tall portrait (9:16)",
    "1:1": "square (1:1)",
    "4:3": "standard (4:3)",
    "auto": "",
}


def _temp_dir() -> Path:
    """Return (and create) a consistent temp directory for generated illustrations."""
    tmp = Path(tempfile.gettempdir()) / "blog_manager_illustrations"
    tmp.mkdir(parents=True, exist_ok=True)
    return tmp


def _generate_illustration_raw(prompt: str) -> str | None:
    """Generate an illustration and return its local file path (or None on failure).

    This is the core implementation used by both the agent tool and the
    save_blog_post safety-net fallback.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return None

    model = os.environ.get("OPENROUTER_IMAGE_MODEL", _DEFAULT_IMAGE_MODEL)

    client = OpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            extra_body={"modalities": ["image", "text"]},
        )

        message = response.choices[0].message
        images = getattr(message, "images", None) or []
        if not images:
            return None

        data_url: str = images[0]["image_url"]["url"]
        if "," in data_url:
            header, b64_data = data_url.split(",", 1)
            ext = "png" if "png" in header else "jpg"
        else:
            b64_data = data_url
            ext = "png"

        img_bytes = base64.b64decode(b64_data)
        tmp_path = _temp_dir() / f"{uuid.uuid4().hex}.{ext}"
        tmp_path.write_bytes(img_bytes)
        return str(tmp_path)

    except Exception:
        return None


@function_tool
def generate_illustration(
    prompt: str,
    aspect_ratio: str = "16:9",
    resolution: str = "1K",
) -> str:
    """Generate an AI illustration via OpenRouter for the blog post.

    Uses google/gemini-3.1-flash-image-preview (configurable via OPENROUTER_IMAGE_MODEL)
    through OpenRouter's chat completions API.  The generated image is saved locally so
    the large base64 payload is never passed back through the LLM context.

    Args:
        prompt: Detailed description of the illustration to generate.
        aspect_ratio: Composition hint — "16:9", "9:16", "1:1", "4:3", or "auto".
        resolution: Ignored (kept for API compatibility). Quality is model-determined.

    Returns:
        JSON string {"url": "<local_file_path>", "description": "..."} on success,
        or {"error": "...", "url": null} on failure.
    """
    if not os.environ.get("OPENROUTER_API_KEY"):
        return json.dumps({"error": "OPENROUTER_API_KEY not set", "url": None})

    # Enrich the prompt with an aspect ratio hint when relevant
    aspect_hint = _ASPECT_HINT.get(aspect_ratio, "")
    full_prompt = f"{prompt}. Composition: {aspect_hint}." if aspect_hint else prompt

    local_path = _generate_illustration_raw(full_prompt)
    if local_path:
        return json.dumps({"url": local_path, "description": prompt})
    return json.dumps({"error": "Image generation failed", "url": None})



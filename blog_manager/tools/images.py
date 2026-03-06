"""Image download utilities."""
from __future__ import annotations

from pathlib import Path

import httpx


def download_image(url: str, dest_dir: Path, filename: str | None = None) -> Path | None:
    """Copy or download an image to a local directory.

    Handles:
    - HTTP/HTTPS URLs  → downloaded with httpx
    - Local file paths → copied directly (used for AI-generated illustrations)

    Returns the destination Path on success, or None on failure.
    """
    if not url:
        return None

    dest_dir.mkdir(parents=True, exist_ok=True)

    # --- Local file path (e.g. temp file from generate_illustration) ---
    local = Path(url)
    if local.exists():
        if filename is None:
            filename = local.name
        dest_path = dest_dir / filename
        dest_path.write_bytes(local.read_bytes())
        return dest_path

    # --- Remote URL ---
    if not url.startswith("http"):
        return None

    if filename is None:
        filename = url.split("/")[-1].split("?")[0]
        if not filename or "." not in filename:
            filename = "image.jpg"

    dest_path = dest_dir / filename

    try:
        with httpx.Client(follow_redirects=True, timeout=30.0) as client:
            response = client.get(url)
            response.raise_for_status()
            dest_path.write_bytes(response.content)
        return dest_path
    except Exception:
        return None

"""Blog file management tools for the Jekyll blog."""
from __future__ import annotations

import html
import json
import re
from datetime import date

from agents import function_tool

from blog_manager.config import ASSETS_IMG_DIR, POSTS_DIR
from blog_manager.tools.images import download_image


def _is_real_url_or_path(url: str) -> bool:
    """Return True if url looks like a real file path or HTTP URL (not a prompt string)."""
    return (
        url.startswith("http://")
        or url.startswith("https://")
        or url.startswith("/tmp/")
        or url.startswith("/var/")
        or url.startswith("./")
        or (len(url) < 200 and ("." in url.split("/")[-1]))
    )


@function_tool
def list_blog_posts() -> str:
    """List the most recent existing blog posts with their filenames."""
    posts = sorted(POSTS_DIR.glob("*.md"))
    result = [{"filename": p.name} for p in posts[-5:]]
    return json.dumps(result)


@function_tool
def read_blog_post(filename: str) -> str:
    """Read the full content of an existing blog post by filename."""
    post_path = POSTS_DIR / filename
    if not post_path.exists():
        return f"Error: Post '{filename}' not found."
    return post_path.read_text(encoding="utf-8")


@function_tool
def save_blog_post(
    title: str,
    description: str,
    tags: str,
    content: str,
    images: str = "[]",
    illustrations: str = "[]",
) -> str:
    """Save a completed blog post to the Jekyll blog.

    Downloads all images/illustrations to the blog's assets directory, replaces
    external URLs in the content with local Jekyll figure includes, and writes the
    post file with proper frontmatter.

    Args:
        title: The blog post title.
        description: Short 1-2 sentence description for frontmatter.
        tags: Tags as a JSON array string, YAML list, comma-separated string, or
            space-separated string.
        content: Full Markdown content WITHOUT frontmatter.
        images: JSON array of {"url": "...", "description": "..."} objects.
        illustrations: JSON array of AI-generated illustration objects.

    Returns:
        JSON with success status, saved file path, and image count.
        On failure returns JSON with success=false and an error message — the caller
        must inspect this and retry after correcting the issue.
    """
    try:
        return _save_blog_post_impl(title, description, tags, content, images, illustrations)
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)})


def _save_blog_post_impl(
    title: str,
    description: str,
    tags: str,
    content: str,
    images: str,
    illustrations: str,
) -> str:
    """Core implementation of save_blog_post — raises on any error."""
    if not POSTS_DIR.parent.exists():
        raise RuntimeError(
            f"Blog directory not accessible: {POSTS_DIR.parent}. "
            "Check that the drive is mounted and the path is correct."
        )

    today = date.today()
    slug = _slugify(title)
    filename = f"{today.strftime('%Y-%m-%d')}-{slug}.md"
    post_path = POSTS_DIR / filename

    img_dir = ASSETS_IMG_DIR / slug
    img_dir.mkdir(parents=True, exist_ok=True)

    normalized_tags = _normalize_tags(tags)

    try:
        image_list: list[dict] = json.loads(images) if isinstance(images, str) else images
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in 'images' parameter: {exc}") from exc

    try:
        illustration_list: list[dict] = (
            json.loads(illustrations) if isinstance(illustrations, str) else illustrations
        )
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in 'illustrations' parameter: {exc}") from exc

    all_images = [
        {"url": img.get("url", ""), "description": img.get("description", ""), "type": "photo"}
        for img in image_list
        if img.get("url")
    ] + [
        {
            "url": img.get("url", ""),
            "description": img.get("description", ""),
            "type": "illustration",
        }
        for img in illustration_list
        if img.get("url")
    ]

    url_to_local: dict[str, str] = {}
    photo_count = 0
    illus_count = 0

    for img in all_images:
        img_url: str = img["url"]
        img_type: str = img["type"]

        # Safety net: if the URL is not a real path/URL (e.g. the Writer embedded
        # an illustration prompt instead of calling generate_illustration first),
        # generate the illustration now.
        if img_type == "illustration" and not _is_real_url_or_path(img_url):
            from blog_manager.tools.illustrator import _generate_illustration_raw
            generated = _generate_illustration_raw(img_url)  # img_url IS the prompt here
            if not generated:
                continue  # skip this image if generation fails
            img_url = generated

        if img_type == "photo":
            photo_count += 1
            idx = photo_count
        else:
            illus_count += 1
            idx = illus_count + len([i for i in all_images if i["type"] == "photo"])

        # Derive a clean extension from the URL (not a prompt string)
        basename = img_url.split("/")[-1].split("?")[0]
        ext = basename.rsplit(".", 1)[-1].lower() if "." in basename else "jpg"
        if ext not in {"jpg", "jpeg", "png", "webp", "gif", "svg"}:
            ext = "jpg"

        local_filename = f"{idx:02d}_{img_type}.{ext}"
        local_path = download_image(img_url, img_dir, local_filename)
        if local_path:
            url_to_local[img_url] = f"assets/img/blog/{slug}/{local_filename}"

    # Strip any leading H1 heading that duplicates the frontmatter title
    stripped = content.lstrip()
    if stripped.startswith("# "):
        first_newline = stripped.find("\n")
        content = stripped[first_newline:].lstrip() if first_newline != -1 else ""

    # Replace markdown image references with Jekyll figure includes
    processed_content = content
    for img_url, rel_path in url_to_local.items():
        processed_content = re.sub(
            rf"!\[([^\]]*)\]\({re.escape(img_url)}\)",
            lambda m, lp=rel_path: _build_figure_include(lp, m.group(1)),
            processed_content,
        )

    # Fallback: match any remaining ![alt](any/path/filename.ext) by filename
    # against files actually saved in the assets dir (handles cases where the
    # agent invented a different path in the content than what was passed in the
    # images/illustrations params).
    saved_by_name = {p.name: f"assets/img/blog/{slug}/{p.name}"
                     for p in img_dir.iterdir() if not p.name.startswith(".")}

    # Use a permissive pattern that captures everything between ![ ... ]( ... )
    # including URLs that contain parentheses (e.g. prompt strings).
    img_re = re.compile(r"!\[([^\]]*)\]\((.+?)\)(?=\s|$|\n|!|\[)", re.DOTALL)

    def _replace_by_filename(m: re.Match) -> str:  # type: ignore[type-arg]
        alt, path = m.group(1), m.group(2).strip()
        fname = path.split("/")[-1].split("?")[0].rstrip(")")
        if fname in saved_by_name:
            rel = saved_by_name[fname]
            return _build_figure_include(rel, alt)
        # URL looks like a prompt string that was never resolved — remove the broken tag
        if not _is_real_url_or_path(path):
            return ""
        return m.group(0)  # leave valid-URL images unchanged

    processed_content = img_re.sub(_replace_by_filename, processed_content)
    tags_frontmatter = (
        "tags: []\n"
        if not normalized_tags
        else "tags:\n" + "\n".join(f"  - {_yaml_quote(tag)}" for tag in normalized_tags) + "\n"
    )

    frontmatter = (
        "---\n"
        "layout: post\n"
        f"title: {_yaml_quote(title)}\n"
        f"date: {today.strftime('%Y-%m-%d')}\n"
        f"description: {_yaml_quote(description)}\n"
        f"{tags_frontmatter}"
        "giscus_comments: true\n"
        "---\n\n"
    )

    POSTS_DIR.mkdir(parents=True, exist_ok=True)
    post_path.write_text(frontmatter + processed_content, encoding="utf-8")

    return json.dumps(
        {
            "success": True,
            "path": str(post_path),
            "filename": filename,
            "images_downloaded": len(url_to_local),
        }
    )


def _slugify(title: str) -> str:
    """Convert a title to a URL-friendly slug."""
    slug = title.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")[:50]


def _normalize_tags(tags: str | list[str]) -> list[str]:
    """Normalize tags from supported tool input formats into a clean list."""
    if isinstance(tags, list):
        raw_tags = tags
    else:
        stripped = tags.strip()
        raw_tags: list[str]
        if not stripped:
            raw_tags = []
        elif stripped.startswith("["):
            parsed = json.loads(stripped)
            raw_tags = parsed if isinstance(parsed, list) else [str(parsed)]
        elif "\n" in stripped and re.search(r"^\s*-\s+", stripped, re.MULTILINE):
            raw_tags = [
                line.split("-", 1)[1].strip()
                for line in stripped.splitlines()
                if line.strip().startswith("-")
            ]
        elif "," in stripped:
            raw_tags = [part.strip() for part in stripped.split(",")]
        else:
            raw_tags = [part.strip() for part in stripped.split()]

    seen: set[str] = set()
    normalized: list[str] = []
    for tag in raw_tags:
        cleaned = str(tag).strip().strip("\"'")
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            normalized.append(cleaned)
    return normalized


def _yaml_quote(value: str) -> str:
    """Return a YAML-safe double-quoted scalar."""
    cleaned = " ".join(str(value).split())
    return json.dumps(cleaned, ensure_ascii=False)


def _build_figure_include(path: str, alt: str) -> str:
    """Build a Jekyll figure include using figure.html."""
    escaped_path = html.escape(path, quote=True)
    escaped_alt = html.escape(alt, quote=True)
    return (
        '{%- include figure.html'
        f' path="{escaped_path}"'
        f' alt="{escaped_alt}"'
        ' class="img-fluid rounded z-depth-1" -%}'
    )

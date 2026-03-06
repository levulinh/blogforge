"""Blog file management tools for the Jekyll blog."""
from __future__ import annotations

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
        tags: Space-separated tags string (e.g. "python ai tutorial").
        content: Full Markdown content WITHOUT frontmatter.
        images: JSON array of {"url": "...", "description": "..."} objects.
        illustrations: JSON array of AI-generated illustration objects.

    Returns:
        JSON with success status, saved file path, and image count.
    """
    today = date.today()
    slug = _slugify(title)
    filename = f"{today.strftime('%Y-%m-%d')}-{slug}.md"
    post_path = POSTS_DIR / filename

    img_dir = ASSETS_IMG_DIR / slug
    img_dir.mkdir(parents=True, exist_ok=True)

    image_list: list[dict] = json.loads(images) if isinstance(images, str) else images
    illustration_list: list[dict] = (
        json.loads(illustrations) if isinstance(illustrations, str) else illustrations
    )

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
        url: str = img["url"]
        img_type: str = img["type"]
        description: str = img.get("description", "")

        # Safety net: if the URL is not a real path/URL (e.g. the Writer embedded
        # an illustration prompt instead of calling generate_illustration first),
        # generate the illustration now.
        if img_type == "illustration" and not _is_real_url_or_path(url):
            from blog_manager.tools.illustrator import _generate_illustration_raw
            generated = _generate_illustration_raw(url)  # url IS the prompt here
            if not generated:
                continue  # skip this image if generation fails
            url = generated

        if img_type == "photo":
            photo_count += 1
            idx = photo_count
        else:
            illus_count += 1
            idx = illus_count + len([i for i in all_images if i["type"] == "photo"])

        # Derive a clean extension from the URL (not a prompt string)
        basename = url.split("/")[-1].split("?")[0]
        ext = basename.rsplit(".", 1)[-1].lower() if "." in basename else "jpg"
        if ext not in {"jpg", "jpeg", "png", "webp", "gif", "svg"}:
            ext = "jpg"

        local_filename = f"{idx:02d}_{img_type}.{ext}"
        local_path = download_image(url, img_dir, local_filename)
        if local_path:
            url_to_local[url] = f"assets/img/blog/{slug}/{local_filename}"

    # Replace markdown image references with Jekyll figure includes
    processed_content = content
    for url, rel_path in url_to_local.items():
        processed_content = re.sub(
            rf"!\[([^\]]*)\]\({re.escape(url)}\)",
            lambda m, lp=rel_path: (
                '{%- include figure.liquid'
                f' path="{lp}"'
                f' alt="{m.group(1)}"'
                ' class="img-fluid rounded z-depth-1" -%}'
            ),
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
            return (
                '{%- include figure.liquid'
                f' path="{rel}"'
                f' alt="{alt}"'
                ' class="img-fluid rounded z-depth-1" -%}'
            )
        # URL looks like a prompt string that was never resolved — remove the broken tag
        if not _is_real_url_or_path(path):
            return ""
        return m.group(0)  # leave valid-URL images unchanged

    processed_content = img_re.sub(_replace_by_filename, processed_content)

    frontmatter = (
        "---\n"
        "layout: post\n"
        f"title: {title}\n"
        f"date: {today.strftime('%Y-%m-%d')}\n"
        f"description: {description}\n"
        f"tags: {tags}\n"
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

"""Multimodal input helper: detect image paths in user text and encode them.

When the user's REPL input references an image file (e.g. "分析一下 @screen.png"
or a bare path "./photo.jpg"), this extracts the path, reads it as base64, and
builds a multimodal content-block list suitable for Anthropic-protocol models
(智谱 GLM / 阶跃 step-3.7-flash) that natively support image input.

If no image is found, returns the plain text string (zero overhead for the
common text-only case).
"""
from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Union

# Match @path or bare relative/absolute paths ending in an image extension.
# Supports @-prefixed (mention style) and quoted/unquoted paths.
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
# @image.png  |  ./x.png  |  C:\x.png  |  /tmp/x.png — but not URLs (http/https)
_IMAGE_PATTERN = re.compile(
    r"(?:@|\./|/|[A-Za-z]:[\\/])?"          # prefix: @ or ./ or / or drive:\
    r"[^\s'\"<>|]+?"                          # the path body (no spaces/quotes)
    r"\.(?:png|jpg|jpeg|gif|webp|bmp)",       # image extension
    re.IGNORECASE,
)

# MIME types
_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
}


def _is_url(s: str) -> bool:
    return s.lower().startswith(("http://", "https://"))


def extract_images(text: str) -> list[tuple[str, str, str]]:
    """Find image file references in text. Returns [(path, media_type, b64data)].

    Only paths that exist on disk are included (silently skips nonexistent ones
    so a stray "@home" doesn't error). URLs are skipped (not fetched here).
    """
    found = []
    seen = set()
    for m in _IMAGE_PATTERN.finditer(text):
        raw = m.group(0)
        # Strip a leading @ (mention syntax) for the actual path.
        path_str = raw[1:] if raw.startswith("@") else raw
        if _is_url(path_str):
            continue
        p = Path(path_str)
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext not in _MIME:
            continue
        resolved = str(p.resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        data = p.read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        found.append((path_str, _MIME[ext], b64))
    return found


def build_user_content(text: str) -> Union[str, list[dict]]:
    """Build the user message content: plain str if no images, or a list of
    content blocks (text + image) for multimodal models.

    The image blocks use the Anthropic content-block format:
        {"type": "image", "source": {"type": "base64", "media_type": ..., "data": ...}}
    which langchain-anthropic's HumanMessage accepts directly.
    """
    images = extract_images(text)
    if not images:
        return text
    blocks: list[dict] = [{"type": "text", "text": text}]
    for path_str, media_type, b64 in images:
        blocks.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": b64},
        })
    return blocks

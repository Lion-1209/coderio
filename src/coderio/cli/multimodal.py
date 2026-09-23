"""Multimodal input helper: detect image paths in user text and encode them.

When the user's REPL input references an image file (e.g. "分析一下 @screen.png"
or a bare path "./photo.jpg"), this extracts the path, reads it as base64, and
builds a multimodal content-block list for models that support image input.

The block SHAPE is protocol-specific (D1-2): Anthropic-style image blocks for
``kind == "anthropic"`` profiles, OpenAI ``image_url`` data-URL blocks for
everything else — see build_user_content. Sending one protocol's shape to the
other is a guaranteed provider 400 with a misleading error.
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
    r"(?:@|\./|/|[A-Za-z]:[\\/])?"  # prefix: @ or ./ or / or drive:\
    r"[^\s'\"<>|]+?"  # the path body (no spaces/quotes)
    r"\.(?:png|jpg|jpeg|gif|webp|bmp)",  # image extension
    re.IGNORECASE,
)

# MIME types
_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
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


def build_user_content(
    text: str,
    images: list[tuple[str, str, str]] | None = None,
    *,
    provider_kind: str = "anthropic",
) -> Union[str, list[dict]]:
    """Build the user message content: plain str if no images, or a list of
    content blocks (text + image) for multimodal models.

    The image block shape is PROTOCOL-SPECIFIC (D1-2, 2026-09-20): sending
    Anthropic-style blocks to an OpenAI-protocol provider is a guaranteed 400
    whose error never mentions the real cause, so the shape follows
    ``provider_kind`` (resolve it with llm.factory.resolved_provider_kind —
    the layer build_chat_model actually uses, not the raw config field):

    - ``"anthropic"``: Anthropic content blocks
      ``{"type": "image", "source": {"type": "base64", ...}}`` which
      langchain-anthropic's HumanMessage accepts directly.
    - anything else (``"openai_compatible"`` and custom providers): OpenAI
      vision blocks ``{"type": "image_url", "image_url": {"url":
      "data:<mime>;base64,..."}}`` which langchain-openai accepts directly.

    A model that genuinely cannot see images surfaces the provider's own
    error through the normal tool-error path (the user sees the provider's
    message, not a silent malformed request).

    ``images`` lets a caller that ALREADY extracted (e.g. the TUI shows the
    attached-file list) reuse that result instead of paying a second
    read+encode of every file (audit P1-18: each image was read + base64'd
    twice per message).
    """
    if images is None:
        images = extract_images(text)
    if not images:
        return text
    blocks: list[dict] = [{"type": "text", "text": text}]
    if provider_kind == "anthropic":
        for path_str, media_type, b64 in images:
            blocks.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": b64},
                }
            )
        return blocks
    # OpenAI-compatible wire format: a data URL inside an image_url block.
    for path_str, media_type, b64 in images:
        blocks.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{media_type};base64,{b64}"},
            }
        )
    return blocks

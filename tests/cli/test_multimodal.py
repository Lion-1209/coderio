"""Tests for multimodal image input helpers (extract_images + build_user_content)."""

import base64
from pathlib import Path

from coderio.cli.multimodal import build_user_content, extract_images


def _make_img(tmp_path: Path, name: str = "test.png", content: bytes = b"\x89PNG fake") -> Path:
    """Create a minimal fake image file."""
    p = tmp_path / name
    p.write_bytes(content)
    return p


def test_extract_images_finds_existing_png(tmp_path):
    """An @-mentioned PNG that exists on disk is extracted with base64 data."""
    img = _make_img(tmp_path, "screen.png")
    text = f"分析一下 @{img}"
    found = extract_images(text)
    assert len(found) == 1
    path_str, media_type, b64 = found[0]
    assert "screen.png" in path_str
    assert media_type == "image/png"
    # base64 decodes back to the original bytes
    assert base64.b64decode(b64) == b"\x89PNG fake"


def test_extract_images_skips_nonexistent(tmp_path):
    """A reference to a file that doesn't exist is silently skipped."""
    text = "@nonexistent.png"
    found = extract_images(text)
    assert found == []


def test_extract_images_skips_urls():
    """URLs with image extensions are not fetched/extracted."""
    text = "看这张图 https://example.com/cat.png"
    found = extract_images(text)
    assert found == []


def test_extract_images_multiple_formats(tmp_path):
    """Different image extensions are all detected."""
    _make_img(tmp_path, "a.jpg", b"JFIF")
    _make_img(tmp_path, "b.gif", b"GIF89a")
    text = f"图1: {tmp_path}/a.jpg 图2: {tmp_path}/b.gif"
    found = extract_images(text)
    assert len(found) == 2
    media_types = {f[1] for f in found}
    assert "image/jpeg" in media_types
    assert "image/gif" in media_types


def test_extract_images_dedupes(tmp_path):
    """The same image mentioned twice is only returned once."""
    img = _make_img(tmp_path, "dup.png")
    text = f"看 @{img} 再看 @{img}"
    found = extract_images(text)
    assert len(found) == 1


def test_build_user_content_plain_text_no_images():
    """When no images are referenced, returns the plain string (zero overhead)."""
    text = "hello world"
    result = build_user_content(text)
    assert result == "hello world"


def test_build_user_content_with_image(tmp_path):
    """When an image is referenced, returns a multimodal content-block list."""
    img = _make_img(tmp_path, "photo.jpg", b"JFIF data")
    text = f"分析这张图 @{img}"
    result = build_user_content(text)
    assert isinstance(result, list)
    assert result[0]["type"] == "text"
    assert result[1]["type"] == "image"
    assert result[1]["source"]["type"] == "base64"
    assert result[1]["source"]["media_type"] == "image/jpeg"


def test_build_user_content_reuses_preextracted_images(tmp_path, monkeypatch):
    """P1-18: build_user_content(text, images=...) must not re-extract — the
    TUI already paid one read+encode per image for its attachment list; the
    old flow encoded every image twice per message."""
    img = _make_img(tmp_path, "reuse.png")
    text = f"看 @{img}"
    pre = extract_images(text)

    calls = []

    def spy_extract(t):
        calls.append(t)
        return extract_images(t)

    monkeypatch.setattr("coderio.cli.multimodal.extract_images", spy_extract)
    content = build_user_content(text, images=pre)
    assert calls == [], "pre-extracted images must bypass a second extraction"
    assert isinstance(content, list) and content[0]["type"] == "text"
    assert content[1] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": pre[0][2]},
    }


# ------------------------------------------------- D1-2: provider-kind gating
def test_build_user_content_openai_kind_uses_image_url_shape(tmp_path):
    """D1-2: OpenAI-protocol providers get image_url data-URL blocks, not
    Anthropic image blocks (the latter is a guaranteed 400 there)."""
    img = _make_img(tmp_path, "photo.jpg", b"JFIF data")
    text = f"分析这张图 @{img}"
    result = build_user_content(text, provider_kind="openai_compatible")
    assert isinstance(result, list)
    assert result[0] == {"type": "text", "text": text}
    assert result[1]["type"] == "image_url"
    assert result[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_build_user_content_anthropic_kind_uses_image_blocks(tmp_path):
    """Anthropic-protocol providers keep the native image-block shape."""
    img = _make_img(tmp_path, "photo.png")
    result = build_user_content(f"看 @{img}", provider_kind="anthropic")
    assert result[1]["type"] == "image"
    assert result[1]["source"]["type"] == "base64"
    assert result[1]["source"]["media_type"] == "image/png"


def test_build_user_content_default_kind_is_anthropic(tmp_path):
    """Back-compat: callers that don't pass provider_kind keep the historical
    Anthropic shape (all existing call sites/tests rely on this default)."""
    img = _make_img(tmp_path, "photo.png")
    result = build_user_content(f"看 @{img}")
    assert result[1]["type"] == "image"


def test_build_user_content_kind_irrelevant_without_images():
    """No images → plain text regardless of kind (zero overhead path)."""
    assert build_user_content("hello", provider_kind="openai_compatible") == "hello"


def test_build_user_content_multiple_images_both_kinds(tmp_path):
    """Every attached image is converted, in order, for either shape."""
    _make_img(tmp_path, "a.png")
    _make_img(tmp_path, "b.jpg", b"JFIF")
    text = f"图1 {tmp_path}/a.png 图2 {tmp_path}/b.jpg"
    anthropic = build_user_content(text, provider_kind="anthropic")
    openai = build_user_content(text, provider_kind="openai_compatible")
    assert [b["type"] for b in anthropic[1:]] == ["image", "image"]
    assert [b["type"] for b in openai[1:]] == ["image_url", "image_url"]
    assert "image/png" in openai[1]["image_url"]["url"]
    assert "image/jpeg" in openai[2]["image_url"]["url"]

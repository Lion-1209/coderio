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

"""Focused tests for handlers._maybe_upscale_asset_image.

The function is deliberately best-effort: a generated image that could not be
inspected/upscaled is still usable and must remain ready. These tests keep
that contract explicit while proving the <1500px trigger itself.
"""

from __future__ import annotations

import pytest

import handlers as h


class _Ctx:
    def __init__(self):
        self.logs: list[tuple[str, str]] = []

    async def log(self, message: str, level: str = "info") -> None:
        self.logs.append((message, level))


def _png(width: int, height: int) -> bytes:
    """Minimum PNG prefix needed by image_dims (signature + IHDR fields)."""
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
    )


@pytest.mark.asyncio
async def test_generated_image_is_copied_to_permanent_storage(monkeypatch):
    ctx = _Ctx()

    class _Stored:
        url = "https://storage.imperal.example/media/featured/original.png"

    class _Storage:
        async def upload(self, path, data, content_type="application/octet-stream"):
            assert path.endswith("/featured/original.png")
            assert content_type == "image/png"
            return _Stored()

    ctx.storage = _Storage()

    async def fake_download(url):
        assert url == "https://cdn.example/original.png?temporary-token"
        return _png(2048, 1536)

    monkeypatch.setattr(h.mc, "download_image_bytes", fake_download)

    result = await h._maybe_upscale_asset_image(
        ctx,
        "key",
        "https://cdn.example/original.png?temporary-token",
        package_id="pkg-1",
        role="featured",
    )

    assert result["original_image_url"] == _Stored.url
    assert result["image_url"] == _Stored.url
    assert result["original_storage_path"] == "media-studio/pkg-1/featured/original.png"


@pytest.mark.asyncio
async def test_small_image_is_auto_upscaled(monkeypatch):
    ctx = _Ctx()
    calls = {}

    async def fake_download(url):
        calls.setdefault("download_urls", []).append(url)
        return _png(1024, 768) if "original" in url else _png(2048, 1536)

    async def fake_upscale(ctx_arg, api_key, image_bytes, scale_factor):
        calls["upscale"] = (ctx_arg, api_key, image_bytes, scale_factor)
        return "https://cdn.example/upscaled.png"

    monkeypatch.setattr(h.mc, "download_image_bytes", fake_download)
    monkeypatch.setattr(h.mc, "upscale_image", fake_upscale)

    result = await h._maybe_upscale_asset_image(ctx, "key", "https://cdn.example/original.png")

    assert result["image_url"] == "https://cdn.example/upscaled.png"
    assert result["original_image_url"] == "https://cdn.example/original.png"
    assert result["original_format"] == "PNG"
    assert result["original_dimensions"] == "1024 × 768 px"
    assert result["original_file_size"]
    assert result["upscaled_image_url"] == "https://cdn.example/upscaled.png"
    assert result["upscaled_format"] == "PNG"
    assert result["upscaled_dimensions"] == "2048 × 1536 px"
    assert result["upscaled_file_size"]
    assert calls["download_urls"] == [
        "https://cdn.example/original.png",
        "https://cdn.example/upscaled.png",
    ]
    assert calls["upscale"][1] == "key"
    assert calls["upscale"][2] == _png(1024, 768)
    assert calls["upscale"][3] == "2x"
    assert ctx.logs == []


@pytest.mark.asyncio
async def test_image_at_1500px_or_larger_is_not_upscaled(monkeypatch):
    ctx = _Ctx()

    async def fake_download(url):
        return _png(2000, 1500)

    async def must_not_upscale(*args, **kwargs):
        raise AssertionError("an image at the threshold must not be upscaled")

    monkeypatch.setattr(h.mc, "download_image_bytes", fake_download)
    monkeypatch.setattr(h.mc, "upscale_image", must_not_upscale)

    original = "https://cdn.example/large.png"
    result = await h._maybe_upscale_asset_image(ctx, "key", original)
    assert result["image_url"] == original
    assert result["original_image_url"] == original
    assert result["original_format"] == "PNG"
    assert result["original_dimensions"] == "2000 × 1500 px"
    assert result["upscaled_image_url"] == ""
    assert ctx.logs == []


@pytest.mark.asyncio
async def test_unknown_image_format_keeps_original_and_logs_warning(monkeypatch):
    ctx = _Ctx()

    async def fake_download(url):
        return b"unknown binary format"

    monkeypatch.setattr(h.mc, "download_image_bytes", fake_download)

    original = "https://cdn.example/unknown.avif"
    result = await h._maybe_upscale_asset_image(ctx, "key", original)
    assert result["image_url"] == original
    assert result["original_image_url"] == original
    assert result["original_format"] == ""
    assert result["original_dimensions"] == ""
    assert ctx.logs and ctx.logs[0][1] == "warning"


@pytest.mark.asyncio
async def test_upscale_failure_keeps_original_and_logs_warning(monkeypatch):
    ctx = _Ctx()

    async def fake_download(url):
        return _png(1024, 768)

    async def failed_upscale(*args, **kwargs):
        raise h.mc.ProviderError("provider unavailable", "MEDIA_PROVIDER_ERROR")

    monkeypatch.setattr(h.mc, "download_image_bytes", fake_download)
    monkeypatch.setattr(h.mc, "upscale_image", failed_upscale)

    original = "https://cdn.example/original.png"
    result = await h._maybe_upscale_asset_image(ctx, "key", original)
    assert result["image_url"] == original
    assert result["original_image_url"] == original
    assert result["original_format"] == "PNG"
    assert result["original_dimensions"] == "1024 × 768 px"
    assert result["upscaled_image_url"] == ""
    assert ctx.logs and ctx.logs[0][1] == "warning"

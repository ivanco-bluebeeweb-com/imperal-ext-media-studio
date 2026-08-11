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
async def test_small_image_is_auto_upscaled(monkeypatch):
    ctx = _Ctx()
    calls = {}

    async def fake_download(url):
        calls["download_url"] = url
        return _png(1024, 768)

    async def fake_upscale(ctx_arg, api_key, image_bytes, scale_factor):
        calls["upscale"] = (ctx_arg, api_key, image_bytes, scale_factor)
        return "https://cdn.example/upscaled.png"

    monkeypatch.setattr(h.mc, "download_image_bytes", fake_download)
    monkeypatch.setattr(h.mc, "upscale_image", fake_upscale)

    result = await h._maybe_upscale_asset_image(ctx, "key", "https://cdn.example/original.png")

    assert result == "https://cdn.example/upscaled.png"
    assert calls["download_url"] == "https://cdn.example/original.png"
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
    assert await h._maybe_upscale_asset_image(ctx, "key", original) == original
    assert ctx.logs == []


@pytest.mark.asyncio
async def test_unknown_image_format_keeps_original_and_logs_warning(monkeypatch):
    ctx = _Ctx()

    async def fake_download(url):
        return b"unknown binary format"

    monkeypatch.setattr(h.mc, "download_image_bytes", fake_download)

    original = "https://cdn.example/unknown.avif"
    assert await h._maybe_upscale_asset_image(ctx, "key", original) == original
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
    assert await h._maybe_upscale_asset_image(ctx, "key", original) == original
    assert ctx.logs and ctx.logs[0][1] == "warning"

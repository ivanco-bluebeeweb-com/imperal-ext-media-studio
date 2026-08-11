"""Manual Upscale Image handler contract."""

from __future__ import annotations

import pytest

import handlers as h
from models import CreateMediaBriefParams, GenerateAssetUpscaleParams


def _png(width: int, height: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
    )


@pytest.mark.asyncio
async def test_manual_upscale_keeps_original_and_saves_upscaled_version(ctx_with_key, monkeypatch):
    brief = await h.create_media_brief(
        ctx_with_key, CreateMediaBriefParams(article_title="Heat pump guide", inline_count=0),
    )
    row = await h.st.get_package(ctx_with_key, brief.data.id)
    row["assets"][0].update({
        "status": "ready",
        "image_url": "https://cdn.example/original.png?token=fresh",
        "original_image_url": "https://cdn.example/original.png?token=fresh",
    })
    await h.st.update_package(ctx_with_key, brief.data.id, {"assets": row["assets"]})

    calls = {}

    async def fake_download(url):
        return _png(1024, 768) if "original" in url else _png(4096, 3072)

    async def fake_upscale(ctx, api_key, image_bytes, scale_factor):
        calls["factor"] = scale_factor
        return "https://cdn.example/upscaled.png?token=fresh"

    monkeypatch.setattr(h.mc, "download_image_bytes", fake_download)
    monkeypatch.setattr(h.mc, "upscale_image", fake_upscale)

    accepted = await h.generate_asset_upscale(
        ctx_with_key,
        GenerateAssetUpscaleParams(package_id=brief.data.id, role="featured", scale_factor="4x"),
    )
    assert accepted.status == "success"
    result = ctx_with_key.last_background_result
    assert result.status == "success"
    assert calls["factor"] == "4x"

    saved = await h.st.get_package(ctx_with_key, brief.data.id)
    asset = saved["assets"][0]
    assert asset["original_image_url"] == "https://cdn.example/original.png?token=fresh"
    assert asset["original_dimensions"] == "1024 × 768 px"
    assert asset["upscaled_image_url"] == "https://cdn.example/upscaled.png?token=fresh"
    assert asset["upscaled_dimensions"] == "4096 × 3072 px"
    assert asset["original_file_size"]
    assert asset["upscaled_file_size"]
    assert asset["image_url"] == asset["upscaled_image_url"]


@pytest.mark.asyncio
async def test_manual_upscale_rejects_factor_not_offered_by_magnific(ctx_with_key):
    brief = await h.create_media_brief(
        ctx_with_key, CreateMediaBriefParams(article_title="Heat pump guide", inline_count=0),
    )
    result = await h.generate_asset_upscale(
        ctx_with_key,
        GenerateAssetUpscaleParams(package_id=brief.data.id, role="featured", scale_factor="3x"),
    )
    assert result.status == "error"
    assert result.error_code == "MEDIA_PROVIDER_ERROR"

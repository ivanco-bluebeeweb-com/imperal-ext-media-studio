"""Tests for the auto model picker and multi-provider routing wired into
the chat-function handlers (create_media_brief / generate_media_package /
regenerate_asset). Complements tests/test_handlers.py, which already
covers the pre-existing Mystic-only behaviour and stays untouched."""

import pytest

import codes as c
import handlers as h
import model_registry as mr
from models import (
    CreateMediaBriefParams,
    GenerateMediaPackageParams,
    RegenerateAssetParams,
)


@pytest.mark.asyncio
async def test_create_media_brief_accepts_auto(ctx):
    result = await h.create_media_brief(
        ctx, CreateMediaBriefParams(
            article_title="Team portrait day", summary="a portrait of our staff",
            inline_count=0, model="auto",
        ),
    )
    assert result.status == "success"
    # package.model keeps the caller's *choice* ("auto"); the concrete
    # per-asset resolution is what actually got picked and stored.
    assert result.data.model == "auto"
    assert result.data.assets[0].model in mr.MODELS


@pytest.mark.asyncio
async def test_create_media_brief_auto_resolves_portrait_prompt_to_gemini(ctx):
    result = await h.create_media_brief(
        ctx, CreateMediaBriefParams(
            article_title="Meet our team", summary="a portrait of our staff",
            inline_count=0, model="auto",
        ),
    )
    assert result.data.assets[0].model == "gemini-2.5-flash"


@pytest.mark.asyncio
async def test_create_media_brief_auto_resolves_featured_to_imagen_ultra(ctx):
    result = await h.create_media_brief(
        ctx, CreateMediaBriefParams(
            article_title="Ventilation systems", summary="a professional photo of ducts",
            inline_count=0, model="auto",
        ),
    )
    assert result.data.assets[0].model == "imagen4-ultra"


@pytest.mark.asyncio
async def test_create_media_brief_accepts_specific_registry_model(ctx):
    result = await h.create_media_brief(
        ctx, CreateMediaBriefParams(article_title="T", summary="S", inline_count=0,
                                     model="imagen4-fast"),
    )
    assert result.status == "success"
    assert result.data.assets[0].model == "imagen4-fast"
    assert result.data.assets[0].provider == "google"


@pytest.mark.asyncio
async def test_create_media_brief_still_rejects_truly_unknown_model(ctx):
    result = await h.create_media_brief(
        ctx, CreateMediaBriefParams(article_title="T", summary="S", inline_count=0,
                                     model="gpt-image-1-5"),
    )
    assert result.status == "error"
    assert result.error_code == c.MEDIA_INVALID_MODEL


@pytest.mark.asyncio
async def test_generate_media_package_routes_registry_model_through_generic_path(
    ctx_with_key, monkeypatch,
):
    brief = await h.create_media_brief(
        ctx_with_key, CreateMediaBriefParams(article_title="T", summary="S", inline_count=0,
                                              model="imagen4-ultra"),
    )
    calls = []

    async def fake_generate_image_with_model(ctx, api_key, prompt, spec, **kwargs):
        calls.append(spec.id)
        return "https://cdn.example/routed.png"

    monkeypatch.setattr(h.mc, "generate_image_with_model", fake_generate_image_with_model)
    await h.generate_media_package(
        ctx_with_key, GenerateMediaPackageParams(package_id=brief.data.id),
    )
    assert calls == ["imagen4-ultra"]
    final = await h.get_media_package(ctx_with_key, __import__("models").GetMediaPackageParams(
        package_id=brief.data.id))
    assert final.data.assets[0].image_url == "https://cdn.example/routed.png"


@pytest.mark.asyncio
async def test_regenerate_asset_accepts_auto_override(ctx_with_key, monkeypatch):
    brief = await h.create_media_brief(
        ctx_with_key, CreateMediaBriefParams(article_title="T", inline_count=0),
    )

    async def fake_generate_image_with_model(ctx, api_key, prompt, spec, **kwargs):
        return "https://cdn.example/auto.png"

    async def fake_generate_image(ctx, api_key, prompt, **kwargs):
        return "https://cdn.example/auto.png"

    monkeypatch.setattr(h.mc, "generate_image_with_model", fake_generate_image_with_model)
    monkeypatch.setattr(h.mc, "generate_image", fake_generate_image)

    result = await h.regenerate_asset(
        ctx_with_key, RegenerateAssetParams(package_id=brief.data.id, role="featured",
                                             model="auto"),
    )
    assert result.status != "error"

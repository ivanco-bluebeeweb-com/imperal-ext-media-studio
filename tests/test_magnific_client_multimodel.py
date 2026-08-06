"""Tests for the generic multi-model provider path (create_job/get_job/
generate_image_with_model) -- the Mystic-only functions already have their
own coverage in test_magnific_client.py and are untouched by this file."""

import pytest

import magnific_client as mc
import model_registry as mr


@pytest.mark.asyncio
async def test_create_job_posts_to_the_spec_path_with_its_own_body(ctx):
    spec = mr.get_model("imagen4-fast")
    ctx.http.mock_post(
        "/v1/ai/text-to-image/imagen4-fast", {"data": {"task_id": "img4-1"}}, status=200,
    )
    task_id = await mc.create_job(ctx, "key", spec, "a warehouse")
    assert task_id == "img4-1"


@pytest.mark.asyncio
async def test_create_job_401_raises_provider_error(ctx):
    spec = mr.get_model("imagen4-ultra")
    ctx.http.mock_post(
        "/v1/ai/text-to-image/imagen4-ultra", {"error": "unauthorized"}, status=401,
    )
    with pytest.raises(mc.ProviderError) as exc:
        await mc.create_job(ctx, "bad-key", spec, "a hero shot")
    assert exc.value.code == "MEDIA_PROVIDER_ERROR"


@pytest.mark.asyncio
async def test_get_job_done_extracts_urls(ctx):
    spec = mr.get_model("gemini-2.5-flash")
    ctx.http.mock_get(
        "/v1/ai/gemini-2-5-flash-image-preview/task-9",
        {"data": {"status": "completed", "generated": ["https://cdn.example/g.png"]}},
    )
    result = await mc.get_job(ctx, "key", spec, "task-9")
    assert result["state"] == "done"
    assert result["image_urls"] == ["https://cdn.example/g.png"]


@pytest.mark.asyncio
async def test_get_job_failed_state(ctx):
    spec = mr.get_model("imagen4-fast")
    ctx.http.mock_get(
        "/v1/ai/text-to-image/imagen4-fast/task-9",
        {"data": {"status": "failed"}},
    )
    result = await mc.get_job(ctx, "key", spec, "task-9")
    assert result["state"] == "failed"


@pytest.mark.asyncio
async def test_generate_image_with_model_returns_first_url(ctx):
    spec = mr.get_model("imagen4-ultra")
    ctx.http.mock_post(
        "/v1/ai/text-to-image/imagen4-ultra", {"data": {"task_id": "t1"}}, status=200,
    )
    ctx.http.mock_get(
        "/v1/ai/text-to-image/imagen4-ultra/t1",
        {"data": {"status": "completed", "generated": ["https://cdn.example/u.png"]}},
    )
    url = await mc.generate_image_with_model(
        ctx, "key", "a hero photo", spec, poll_interval_s=0,
    )
    assert url == "https://cdn.example/u.png"


@pytest.mark.asyncio
async def test_generate_image_with_model_raises_on_provider_failure(ctx):
    spec = mr.get_model("gemini-2.5-flash")
    ctx.http.mock_post(
        "/v1/ai/gemini-2-5-flash-image-preview", {"data": {"task_id": "t2"}}, status=200,
    )
    ctx.http.mock_get(
        "/v1/ai/gemini-2-5-flash-image-preview/t2",
        {"data": {"status": "failed"}},
    )
    with pytest.raises(mc.ProviderError) as exc:
        await mc.generate_image_with_model(ctx, "key", "a portrait", spec, poll_interval_s=0)
    assert exc.value.code == "MEDIA_PROVIDER_ERROR"

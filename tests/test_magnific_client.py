import pytest

import magnific_client as mc


@pytest.mark.asyncio
async def test_create_mystic_job_extracts_task_id(ctx):
    ctx.http.mock_post("/v1/ai/mystic", {"data": {"task_id": "abc123"}}, status=200)
    task_id = await mc.create_mystic_job(ctx, "key", "a cat")
    assert task_id == "abc123"


@pytest.mark.asyncio
async def test_create_mystic_job_with_model_still_works(ctx):
    """model is opt-in -- passing one must not change the call's success path."""
    ctx.http.mock_post("/v1/ai/mystic", {"data": {"task_id": "abc123"}}, status=200)
    task_id = await mc.create_mystic_job(ctx, "key", "a cat", model="fluid")
    assert task_id == "abc123"


@pytest.mark.asyncio
async def test_create_mystic_job_401_raises_provider_error(ctx):
    ctx.http.mock_post("/v1/ai/mystic", {"error": "unauthorized"}, status=401)
    with pytest.raises(mc.ProviderError) as exc:
        await mc.create_mystic_job(ctx, "bad-key", "a cat")
    assert exc.value.code == "MEDIA_PROVIDER_ERROR"


@pytest.mark.asyncio
async def test_create_mystic_job_no_task_id_raises(ctx):
    ctx.http.mock_post("/v1/ai/mystic", {"data": {}}, status=200)
    with pytest.raises(mc.ProviderError):
        await mc.create_mystic_job(ctx, "key", "a cat")


@pytest.mark.asyncio
async def test_get_mystic_task_done_extracts_urls(ctx):
    ctx.http.mock_get(
        "/v1/ai/mystic/abc123",
        {"data": {"status": "completed", "generated": ["https://cdn.example/a.png"]}},
    )
    result = await mc.get_mystic_task(ctx, "key", "abc123")
    assert result["state"] == "done"
    assert result["image_urls"] == ["https://cdn.example/a.png"]


@pytest.mark.asyncio
async def test_get_mystic_task_done_without_urls_raises(ctx):
    ctx.http.mock_get("/v1/ai/mystic/abc123", {"data": {"status": "completed"}})
    with pytest.raises(mc.ProviderError):
        await mc.get_mystic_task(ctx, "key", "abc123")


@pytest.mark.asyncio
async def test_get_mystic_task_failed_state(ctx):
    ctx.http.mock_get("/v1/ai/mystic/abc123", {"data": {"status": "failed"}})
    result = await mc.get_mystic_task(ctx, "key", "abc123")
    assert result["state"] == "failed"


@pytest.mark.asyncio
async def test_get_mystic_task_pending_state(ctx):
    ctx.http.mock_get("/v1/ai/mystic/abc123", {"data": {"status": "processing"}})
    result = await mc.get_mystic_task(ctx, "key", "abc123")
    assert result["state"] == "pending"
    assert result["raw_status"] == "processing"


@pytest.mark.asyncio
async def test_generate_image_happy_path(ctx):
    ctx.http.mock_post("/v1/ai/mystic", {"data": {"task_id": "t1"}})
    ctx.http.mock_get(
        "/v1/ai/mystic/t1",
        {"data": {"status": "completed", "generated": ["https://cdn.example/img.png"]}},
    )
    url = await mc.generate_image(ctx, "key", "a cat", poll_interval_s=0, max_polls=3)
    assert url == "https://cdn.example/img.png"


@pytest.mark.asyncio
async def test_generate_image_failed_job_raises(ctx):
    ctx.http.mock_post("/v1/ai/mystic", {"data": {"task_id": "t1"}})
    ctx.http.mock_get("/v1/ai/mystic/t1", {"data": {"status": "failed"}})
    with pytest.raises(mc.ProviderError):
        await mc.generate_image(ctx, "key", "a cat", poll_interval_s=0, max_polls=3)


@pytest.mark.asyncio
async def test_generate_image_timeout_when_never_done(ctx):
    ctx.http.mock_post("/v1/ai/mystic", {"data": {"task_id": "t1"}})
    ctx.http.mock_get("/v1/ai/mystic/t1", {"data": {"status": "processing"}})
    with pytest.raises(mc.ProviderError) as exc:
        await mc.generate_image(ctx, "key", "a cat", poll_interval_s=0, max_polls=2)
    assert exc.value.code == "MEDIA_PROVIDER_TIMEOUT"


@pytest.mark.asyncio
async def test_generate_image_calls_on_progress(ctx):
    ctx.http.mock_post("/v1/ai/mystic", {"data": {"task_id": "t1"}})
    ctx.http.mock_get(
        "/v1/ai/mystic/t1",
        {"data": {"status": "completed", "generated": ["https://cdn.example/img.png"]}},
    )
    seen = []

    async def on_progress(attempt, max_polls):
        seen.append((attempt, max_polls))

    await mc.generate_image(ctx, "key", "a cat", poll_interval_s=0, max_polls=3, on_progress=on_progress)
    assert seen == [(1, 3)]

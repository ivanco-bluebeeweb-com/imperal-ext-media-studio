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


# --------------------------- sync_base64 path (Classic Fast) ---------------------------

def _classic_fast_spec():
    import model_registry as mr
    return mr.MODELS["classic-fast"]


@pytest.mark.asyncio
async def test_create_sync_image_uploads_decoded_bytes_and_returns_url(ctx, monkeypatch):
    import base64
    raw = b"\x89PNG\r\n\x1a\nfake-bytes"
    b64 = base64.b64encode(raw).decode()
    ctx.http.mock_post("/v1/ai/text-to-image", {"data": [{"base64": b64, "has_nsfw": False}]})

    # MockStorage (imperal_sdk.testing) never populates FileInfo.url -- only
    # the REAL gateway does (it parses `url` out of the upload response, see
    # StorageClient.upload). Patch just that one return value here so the
    # test exercises the real "no url -> raise" guard against a REALISTIC
    # success shape, not the mock's incomplete default.
    from imperal_sdk.types.models import FileInfo
    captured = {}
    real_upload = ctx.storage.upload

    async def fake_upload(path, data, content_type="application/octet-stream"):
        captured["data"] = data
        return FileInfo(path=path, size=len(data), content_type=content_type,
                         url="https://cdn.example/uploaded.png")

    monkeypatch.setattr(ctx.storage, "upload", fake_upload)

    url = await mc.create_sync_image(ctx, "key", _classic_fast_spec(), "a cat")
    assert url == "https://cdn.example/uploaded.png"
    # The exact bytes actually reached storage -- proves no silent corruption
    # in the base64 decode -> upload path.
    assert captured["data"] == raw


@pytest.mark.asyncio
async def test_create_sync_image_relative_storage_path_raises(ctx, monkeypatch):
    """Regression test for the 2026-08-13 g4s.md bug: storage.upload()
    returning a non-empty but non-absolute value (a bare relative path,
    not an https:// URL) must be rejected here, not silently persisted --
    otherwise WordPress Bridge later rejects it downstream with
    'source_url must be a well-formed URL' and the picture never attaches.
    """
    import base64
    raw = b"\x89PNG\r\n\x1a\nfake-bytes"
    b64 = base64.b64encode(raw).decode()
    ctx.http.mock_post("/v1/ai/text-to-image", {"data": [{"base64": b64, "has_nsfw": False}]})

    from imperal_sdk.types.models import FileInfo

    async def fake_upload(path, data, content_type="application/octet-stream"):
        return FileInfo(path=path, size=len(data), content_type=content_type,
                         url="media-studio/pkg-1/featured/original.png")

    monkeypatch.setattr(ctx.storage, "upload", fake_upload)

    with pytest.raises(mc.ProviderError) as exc:
        await mc.create_sync_image(ctx, "key", _classic_fast_spec(), "a cat")
    assert exc.value.code == "MEDIA_PROVIDER_ERROR"


@pytest.mark.asyncio
async def test_create_sync_image_401_raises_provider_error(ctx):
    ctx.http.mock_post("/v1/ai/text-to-image", {"error": "unauthorized"}, status=401)
    with pytest.raises(mc.ProviderError) as exc:
        await mc.create_sync_image(ctx, "bad-key", _classic_fast_spec(), "a cat")
    assert exc.value.code == "MEDIA_PROVIDER_ERROR"


@pytest.mark.asyncio
async def test_create_sync_image_no_base64_raises(ctx):
    ctx.http.mock_post("/v1/ai/text-to-image", {"data": [{"has_nsfw": False}]})
    with pytest.raises(mc.ProviderError):
        await mc.create_sync_image(ctx, "key", _classic_fast_spec(), "a cat")


@pytest.mark.asyncio
async def test_create_sync_image_bad_base64_raises(ctx):
    ctx.http.mock_post("/v1/ai/text-to-image", {"data": [{"base64": "not-valid-base64!!"}]})
    with pytest.raises(mc.ProviderError):
        await mc.create_sync_image(ctx, "key", _classic_fast_spec(), "a cat")

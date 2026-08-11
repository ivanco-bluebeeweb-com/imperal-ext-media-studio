"""Tests for magnific_client's Upscaler Creative (auto-upscale) support."""

from __future__ import annotations

import base64

import httpx
import pytest

import magnific_client as mc


# --------------------------- upscale_scale_factor_for ---------------------------

@pytest.mark.parametrize(
    "width,height,expected",
    [
        (1600, 1600, None),   # already clears both sides
        (1500, 1500, None),   # exactly at threshold -- no upscale needed
        (1499, 1499, "2x"),   # just under -- smallest factor that clears
        (1024, 768, "2x"),    # typical text-to-image output, smaller side 768
        (500, 500, "4x"),     # 500*2=1000 still <1500, needs 4x
        (100, 100, "16x"),    # needs the largest documented factor
        (50, 50, None),       # 50*16=800 still <1500 -- no legal factor, must not force a doomed request
        (4000, 4000, None),   # far above threshold
    ],
)
def test_scale_factor_selection(width, height, expected):
    assert mc.upscale_scale_factor_for(width, height, min_side=1500) == expected


def test_scale_factor_respects_output_pixel_cap():
    # A factor that would legally clear min_side but blow past the documented
    # 25.3-megapixel output cap must be skipped in favour of a smaller one,
    # or None if even the smallest breaches the cap.
    # 3000x3000 already clears 1500 on both sides -> None regardless of cap.
    assert mc.upscale_scale_factor_for(3000, 3000, min_side=1500) is None
    # 1400x1400 needs 2x (2800x2800=7.84MP, under cap) -> should pick 2x, not skip.
    assert mc.upscale_scale_factor_for(1400, 1400, min_side=1500) == "2x"


# --------------------------- download_image_bytes ---------------------------

@pytest.mark.asyncio
async def test_download_image_bytes_returns_raw_bytes(monkeypatch):
    real_png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(range(200))

    class FakeResponse:
        status_code = 200
        content = real_png_bytes

    class FakeAsyncClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return FakeResponse()

    monkeypatch.setattr(mc.httpx, "AsyncClient", FakeAsyncClient)
    data = await mc.download_image_bytes("https://cdn.example/img.png")
    assert data == real_png_bytes


@pytest.mark.asyncio
async def test_download_image_bytes_raises_on_error_status(monkeypatch):
    class FakeResponse:
        status_code = 404
        content = b""

    class FakeAsyncClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return FakeResponse()

    monkeypatch.setattr(mc.httpx, "AsyncClient", FakeAsyncClient)
    with pytest.raises(mc.ProviderError):
        await mc.download_image_bytes("https://cdn.example/missing.png")


# --------------------------- create_upscale_job / get_upscale_job ---------------------------

class _FakeHTTPResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class _FakeCtxHTTP:
    """Mimics ctx.http for JSON-only calls (create/get job status use JSON,
    never raw image bytes, so the real corruption bug doesn't apply here)."""

    def __init__(self, post_response=None, get_response=None):
        self._post_response = post_response
        self._get_response = get_response
        self.last_post_json = None

    async def post(self, url, headers=None, json=None, timeout=None):
        self.last_post_json = json
        return self._post_response

    async def get(self, url, headers=None, timeout=None):
        return self._get_response


class _FakeCtx:
    def __init__(self, http):
        self.http = http


@pytest.mark.asyncio
async def test_create_upscale_job_sends_base64_and_scale_factor():
    fake_http = _FakeCtxHTTP(
        post_response=_FakeHTTPResponse(200, {"data": {"task_id": "up_123"}}),
    )
    ctx = _FakeCtx(fake_http)
    image_bytes = b"\xff\xd8\xff\xe0fake jpeg bytes"
    task_id = await mc.create_upscale_job(ctx, "key123", image_bytes, "2x")
    assert task_id == "up_123"
    sent = fake_http.last_post_json
    assert sent["scale_factor"] == "2x"
    assert base64.b64decode(sent["image"]) == image_bytes


@pytest.mark.asyncio
async def test_create_upscale_job_raises_on_error_status():
    fake_http = _FakeCtxHTTP(post_response=_FakeHTTPResponse(500, {}))
    ctx = _FakeCtx(fake_http)
    with pytest.raises(mc.ProviderError):
        await mc.create_upscale_job(ctx, "key123", b"data", "2x")


@pytest.mark.asyncio
async def test_get_upscale_job_done_returns_generated_urls():
    fake_http = _FakeCtxHTTP(
        get_response=_FakeHTTPResponse(
            200, {"data": {"status": "completed", "generated": ["https://cdn/upscaled.png"]}},
        ),
    )
    ctx = _FakeCtx(fake_http)
    result = await mc.get_upscale_job(ctx, "key123", "up_123")
    assert result["state"] == "done"
    assert result["image_urls"] == ["https://cdn/upscaled.png"]


@pytest.mark.asyncio
async def test_get_upscale_job_pending():
    fake_http = _FakeCtxHTTP(
        get_response=_FakeHTTPResponse(200, {"data": {"status": "in_progress"}}),
    )
    ctx = _FakeCtx(fake_http)
    result = await mc.get_upscale_job(ctx, "key123", "up_123")
    assert result["state"] == "pending"


@pytest.mark.asyncio
async def test_get_upscale_job_failed():
    fake_http = _FakeCtxHTTP(
        get_response=_FakeHTTPResponse(200, {"data": {"status": "failed"}}),
    )
    ctx = _FakeCtx(fake_http)
    result = await mc.get_upscale_job(ctx, "key123", "up_123")
    assert result["state"] == "failed"


@pytest.mark.asyncio
async def test_upscale_image_polls_until_done(monkeypatch):
    calls = {"n": 0}

    async def fake_create_upscale_job(ctx, api_key, image_bytes, scale_factor):
        return "up_999"

    async def fake_get_upscale_job(ctx, api_key, task_id):
        calls["n"] += 1
        if calls["n"] < 2:
            return {"state": "pending", "image_urls": [], "raw_status": "in_progress"}
        return {"state": "done", "image_urls": ["https://cdn/final.png"], "raw_status": "completed"}

    monkeypatch.setattr(mc, "create_upscale_job", fake_create_upscale_job)
    monkeypatch.setattr(mc, "get_upscale_job", fake_get_upscale_job)

    ctx = _FakeCtx(_FakeCtxHTTP())
    url = await mc.upscale_image(ctx, "key123", b"bytes", "2x", poll_interval_s=0, max_polls=5)
    assert url == "https://cdn/final.png"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_upscale_image_raises_on_failed_status(monkeypatch):
    async def fake_create_upscale_job(ctx, api_key, image_bytes, scale_factor):
        return "up_999"

    async def fake_get_upscale_job(ctx, api_key, task_id):
        return {"state": "failed", "image_urls": [], "raw_status": "failed"}

    monkeypatch.setattr(mc, "create_upscale_job", fake_create_upscale_job)
    monkeypatch.setattr(mc, "get_upscale_job", fake_get_upscale_job)

    ctx = _FakeCtx(_FakeCtxHTTP())
    with pytest.raises(mc.ProviderError):
        await mc.upscale_image(ctx, "key123", b"bytes", "2x", poll_interval_s=0, max_polls=5)

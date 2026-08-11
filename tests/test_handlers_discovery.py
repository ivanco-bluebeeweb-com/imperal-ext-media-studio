"""Tests for the check_new_models / list_model_discovery_log chat tools and
the daily media_model_discovery schedule tick.

WHY THE HTTP MOCK PATCHES HTTPResponse.text INSTEAD OF USING mock_get's BODY.

MockHTTP always stores whatever dict you pass to mock_get as `.body`, and
HTTPResponse.text() falls back to json.dumps() for a non-str body -- which
would mangle real XML. Patching `.text()` directly is the one way to hand
back a raw XML string through this mock layer (see test_model_discovery.py,
same pattern, already proven there).
"""

from __future__ import annotations

import pytest

import handlers_discovery as hd
import model_discovery as md
import model_registry as mr


_SITEMAP_NO_NEW = """<?xml version="1.0"?>
<urlset>
  <url><loc>https://docs.magnific.com/api-reference/text-to-image/post-nano-banana-pro</loc></url>
</urlset>"""

_SITEMAP_WITH_NEW = """<?xml version="1.0"?>
<urlset>
  <url><loc>https://docs.magnific.com/api-reference/text-to-image/post-nano-banana-pro</loc></url>
  <url><loc>https://docs.magnific.com/api-reference/text-to-image/brand-new-thing/overview</loc></url>
  <url><loc>https://docs.magnific.com/api-reference/text-to-image/brand-new-thing/generate</loc></url>
</urlset>"""


def _mock_sitemap(ctx, monkeypatch, body: str, status: int = 200) -> None:
    from imperal_sdk.types.models import HTTPResponse
    ctx.http.mock_get("sitemap.xml", {}, status=status)
    monkeypatch.setattr(HTTPResponse, "text", lambda self: body)


@pytest.mark.asyncio
async def test_check_new_models_reports_nothing_new(ctx, monkeypatch):
    _mock_sitemap(ctx, monkeypatch, _SITEMAP_NO_NEW)
    result = await hd.check_new_models(ctx, hd.CheckNewModelsParams())
    assert result.status == "success"
    assert result.data.source_reachable is True
    assert result.data.new_candidates == []
    assert result.data.known_model_count == len(mr.MODELS)


@pytest.mark.asyncio
async def test_check_new_models_reports_a_real_finding(ctx, monkeypatch):
    _mock_sitemap(ctx, monkeypatch, _SITEMAP_WITH_NEW)
    result = await hd.check_new_models(ctx, hd.CheckNewModelsParams())
    assert result.status == "success"
    slugs = [f.slug for f in result.data.new_candidates]
    assert slugs == ["brand-new-thing"]
    assert result.data.new_candidates[0].docs_url.startswith("https://docs.magnific.com/")


@pytest.mark.asyncio
async def test_check_new_models_fetch_failure_is_a_clean_error_not_a_crash(ctx):
    ctx.http.mock_get("sitemap.xml", {"error": "boom"}, status=500)
    result = await hd.check_new_models(ctx, hd.CheckNewModelsParams())
    assert result.status == "error"


@pytest.mark.asyncio
async def test_check_new_models_always_writes_a_log_entry_even_when_clean(ctx, monkeypatch):
    _mock_sitemap(ctx, monkeypatch, _SITEMAP_NO_NEW)
    await hd.check_new_models(ctx, hd.CheckNewModelsParams())
    rows = await md.list_log(ctx, limit=10)
    assert len(rows) == 1
    assert rows[0]["result"] == "no_new"


@pytest.mark.asyncio
async def test_list_model_discovery_log_reflects_past_runs(ctx, monkeypatch):
    _mock_sitemap(ctx, monkeypatch, _SITEMAP_WITH_NEW)
    await hd.check_new_models(ctx, hd.CheckNewModelsParams())
    result = await hd.list_model_discovery_log(ctx, hd.ListModelDiscoveryLogParams(limit=10))
    assert result.status == "success"
    assert len(result.data.items) == 1
    assert result.data.items[0].new_candidate_slugs == ["brand-new-thing"]


@pytest.mark.asyncio
async def test_scheduled_tick_skips_when_not_due(ctx, monkeypatch):
    """due() gates on CHECK_HOUR_UTC/last_date -- force it False and prove
    the tick makes NO http call at all (the alarm-clock discipline)."""
    async def fake_due(ctx, **kw):
        return False
    monkeypatch.setattr(md, "due", fake_due)
    await hd.media_model_discovery(ctx)
    # No sitemap mock registered at all -- if the tick tried to fetch it,
    # MockHTTP would 404 and find_new_models would raise, failing the test.
    rows = await md.list_log(ctx, limit=10)
    assert rows == []


@pytest.mark.asyncio
async def test_scheduled_tick_runs_and_logs_when_due(ctx, monkeypatch):
    async def fake_due(ctx, **kw):
        return True
    monkeypatch.setattr(md, "due", fake_due)
    _mock_sitemap(ctx, monkeypatch, _SITEMAP_WITH_NEW)
    await hd.media_model_discovery(ctx)
    rows = await md.list_log(ctx, limit=10)
    assert len(rows) == 1
    assert rows[0]["found"] == ["brand-new-thing"]

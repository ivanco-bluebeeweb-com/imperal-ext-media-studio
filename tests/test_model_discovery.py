"""Tests for the daily new-model discovery check.

WHY THE FAKE SITEMAP MIXES KNOWN AND UNKNOWN URLS.

A realistic sitemap has both: pages for models we already integrated
(should NOT be reported) and, eventually, a page for something new
(should be reported). Mixing them in one fixture is what actually proves
`find_new_models` filters correctly instead of just returning everything.
"""

from __future__ import annotations

import time

import pytest

import model_discovery as md


_FAKE_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset>
  <url><loc>https://docs.magnific.com/api-reference/mystic/mystic</loc></url>
  <url><loc>https://docs.magnific.com/api-reference/text-to-image/imagen4/overview</loc></url>
  <url><loc>https://docs.magnific.com/api-reference/text-to-image/post-nano-banana-pro</loc></url>
  <url><loc>https://docs.magnific.com/api-reference/text-to-image/get-nano-banana-pro</loc></url>
  <url><loc>https://docs.magnific.com/api-reference/text-to-image/totally-new-model/overview</loc></url>
  <url><loc>https://docs.magnific.com/api-reference/text-to-image/totally-new-model/generate</loc></url>
  <url><loc>https://docs.magnific.com/api-reference/text-to-image/totally-new-model/get-totally-new-model-task</loc></url>
</urlset>"""


@pytest.mark.asyncio
async def test_fetch_candidate_slugs_finds_new_model_and_skips_known(ctx, monkeypatch):
    from imperal_sdk.types.models import HTTPResponse

    ctx.http.mock_get("sitemap.xml", {}, status=200)
    monkeypatch.setattr(HTTPResponse, "text", lambda self: _FAKE_SITEMAP)

    slugs = await md.fetch_candidate_slugs(ctx)
    assert "totally-new-model" in slugs
    assert "nano-banana-pro" in slugs  # present in sitemap, filtered out LATER (by find_new_models), not here
    assert "imagen4" in slugs  # a real overview-page slug -- filtered out LATER via EXCLUDED_SLUGS, not here


@pytest.mark.asyncio
async def test_find_new_models_excludes_already_registered(ctx, monkeypatch):
    from imperal_sdk.types.models import HTTPResponse

    ctx.http.mock_get("sitemap.xml", {}, status=200)
    monkeypatch.setattr(HTTPResponse, "text", lambda self: _FAKE_SITEMAP)

    new = await md.find_new_models(ctx)
    assert new == ["totally-new-model"]


@pytest.mark.asyncio
async def test_find_new_models_excludes_reviewed_and_declined_slugs(ctx, monkeypatch):
    """imagen4 has a real docs page (the imagen4-fast/imagen4-ultra parent
    overview) but was explicitly reviewed and declined -- it must never be
    reported as if it were a fresh, unreviewed finding."""
    from imperal_sdk.types.models import HTTPResponse

    ctx.http.mock_get("sitemap.xml", {}, status=200)
    monkeypatch.setattr(HTTPResponse, "text", lambda self: _FAKE_SITEMAP)

    new = await md.find_new_models(ctx)
    assert "imagen4" not in new


def test_classic_fast_registered_under_its_own_model_id_not_generic_path():
    """classic-fast's create_path is the bare /v1/ai/text-to-image (no
    per-model path segment) -- _known_create_slugs must map it to the model
    id itself, not the meaningless basename \"text-to-image\"."""
    import model_registry as mr
    assert "classic-fast" in md._known_create_slugs()


@pytest.mark.asyncio
async def test_fetch_candidate_slugs_raises_on_http_failure(ctx):
    ctx.http.mock_get("sitemap.xml", {"error": "nope"}, status=500)
    with pytest.raises(RuntimeError):
        await md.fetch_candidate_slugs(ctx)


@pytest.mark.asyncio
async def test_due_false_before_check_hour(ctx):
    import calendar
    # 03:00 UTC is before CHECK_HOUR_UTC (6) -- must not be due yet.
    early_ts = calendar.timegm(time.strptime("2026-08-11 03:00:00", "%Y-%m-%d %H:%M:%S"))
    assert await md.due(ctx, ts=early_ts) is False


@pytest.mark.asyncio
async def test_due_true_after_check_hour_first_time(ctx):
    import calendar
    ts = calendar.timegm(time.strptime("2026-08-11 07:00:00", "%Y-%m-%d %H:%M:%S"))
    assert await md.due(ctx, ts=ts) is True


@pytest.mark.asyncio
async def test_due_false_twice_same_day(ctx):
    import calendar
    ts = calendar.timegm(time.strptime("2026-08-11 07:00:00", "%Y-%m-%d %H:%M:%S"))
    await md.record_check(ctx, found=[], result="no_new", ts=ts)
    later_same_day = calendar.timegm(time.strptime("2026-08-11 09:00:00", "%Y-%m-%d %H:%M:%S"))
    assert await md.due(ctx, ts=later_same_day) is False


@pytest.mark.asyncio
async def test_due_true_again_next_day(ctx):
    import calendar
    ts = calendar.timegm(time.strptime("2026-08-11 07:00:00", "%Y-%m-%d %H:%M:%S"))
    await md.record_check(ctx, found=[], result="no_new", ts=ts)
    next_day = calendar.timegm(time.strptime("2026-08-12 07:00:00", "%Y-%m-%d %H:%M:%S"))
    assert await md.due(ctx, ts=next_day) is True


@pytest.mark.asyncio
async def test_record_check_is_permanent_log_not_overwritten(ctx):
    await md.record_check(ctx, found=["model-a"], result="found_new", ts=1000.0)
    await md.record_check(ctx, found=[], result="no_new", ts=2000.0)
    rows = await md.list_log(ctx, limit=10)
    assert len(rows) == 2  # both checks kept, not overwritten
    assert rows[0]["checked_at"] == 2000.0  # newest first


@pytest.mark.asyncio
async def test_list_log_respects_limit(ctx):
    for i in range(5):
        await md.record_check(ctx, found=[], result="no_new", ts=float(i))
    rows = await md.list_log(ctx, limit=2)
    assert len(rows) == 2

"""Safety contracts for legacy Media Hub image recovery."""

from __future__ import annotations

import pytest

import recovery


def _creation(reference: str, url: str) -> dict:
    return {
        "reference": reference,
        "external_id": "8421",
        "creation": {"id": 8421, "identifier": "a1b2c3d4", "url": url},
    }


def test_exact_creation_reference_match_restores_source_url():
    reference = "f992763b-cdaa-474c-8329-2ed967529295"
    assets = [{
        "role": "featured",
        "image_url": f"https://cdn-magnific.freepik.com/result_IMAGEN4_{reference}_0.png?token=expired",
    }]
    creations = [_creation(reference, "https://cdn.freepik.com/creations/8421.png")]

    assert recovery.match_creation_urls(assets, creations) == {
        "featured": "https://cdn.freepik.com/creations/8421.png",
    }


def test_unrelated_creation_is_never_matched():
    assets = [{
        "role": "featured",
        "image_url": "https://cdn.example/result_f992763b-cdaa-474c-8329-2ed967529295.png?token=old",
    }]
    creations = [_creation("9ed2a4cf-35a7-4e43-a6de-7e4d5366d858", "https://cdn.example/other.png")]

    assert recovery.match_creation_urls(assets, creations) == {}


def test_ambiguous_multiple_source_urls_are_never_matched():
    reference = "f992763b-cdaa-474c-8329-2ed967529295"
    assets = [{"role": "featured", "image_url": f"https://cdn.example/{reference}.png"}]
    creations = [
        _creation(reference, "https://cdn.example/first.png"),
        _creation(reference, "https://cdn.example/second.png"),
    ]

    assert recovery.match_creation_urls(assets, creations) == {}


@pytest.mark.asyncio
async def test_historic_mystic_task_returns_its_single_image_url(monkeypatch, ctx):
    async def fake_task(_ctx, _api_key, task_id):
        assert task_id == "f992763b-cdaa-474c-8329-2ed967529295"
        return {"state": "done", "image_urls": ["https://cdn.example/exact-image.png"]}

    monkeypatch.setattr(recovery.mc, "get_mystic_task", fake_task)

    assert await recovery.get_mystic_task_image_url(
        ctx, "key", "f992763b-cdaa-474c-8329-2ed967529295",
    ) == "https://cdn.example/exact-image.png"


@pytest.mark.asyncio
async def test_historic_mystic_task_refuses_nonfinal_or_ambiguous_results(monkeypatch, ctx):
    async def pending_task(_ctx, _api_key, _task_id):
        return {"state": "pending", "image_urls": []}

    monkeypatch.setattr(recovery.mc, "get_mystic_task", pending_task)
    assert await recovery.get_mystic_task_image_url(ctx, "key", "task") == ""


@pytest.mark.asyncio
async def test_recent_creations_paginates_and_keeps_records(ctx):
    calls: list[dict] = []

    class Response:
        status_code = 200

        def __init__(self, records):
            self._records = records

        def json(self):
            return {"data": self._records}

    first = _creation("one-reference", "https://cdn.example/one.png")
    second = _creation("two-reference", "https://cdn.example/two.png")

    async def fake_get(url, *, headers, params, timeout):
        calls.append(params)
        return Response([first] * 100 if params["page"] == 1 else [second])

    ctx.http.get = fake_get
    records = await recovery.list_recent_creations(ctx, "key")

    assert records == [first] * 100 + [second]
    assert [call["page"] for call in calls] == [1, 2]

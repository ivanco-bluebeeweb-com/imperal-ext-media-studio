"""Safety contracts for legacy Media Hub image recovery."""

from __future__ import annotations

import pytest

import recovery


def test_exact_filename_match_ignores_expiring_query_token():
    assets = [{
        "role": "featured",
        "image_url": "https://cdn-magnific.freepik.com/result_ABC.png?token=exp=1~hmac=old",
    }]
    urls = ["https://cdn.freepik.com/creations/result_ABC.png?fresh=yes"]

    assert recovery.match_creation_urls(assets, urls) == {
        "featured": "https://cdn.freepik.com/creations/result_ABC.png?fresh=yes",
    }


def test_ambiguous_filename_is_never_matched():
    assets = [{"role": "featured", "image_url": "https://cdn.example/result_ABC.png?token=old"}]
    urls = [
        "https://cdn.example/a/result_ABC.png?fresh=1",
        "https://cdn.example/b/result_ABC.png?fresh=2",
    ]

    assert recovery.match_creation_urls(assets, urls) == {}


def test_task_id_requires_a_matching_provider_identifier():
    assets = [{
        "role": "featured",
        "provider_task_id": "task-12345678",
        "image_url": "https://cdn.example/result_ABC.png?token=old",
    }]
    urls = ["https://cdn.example/task-12345678/result_ABC.png?fresh=1"]

    assert recovery.match_creation_urls(assets, urls) == {
        "featured": "https://cdn.example/task-12345678/result_ABC.png?fresh=1",
    }


@pytest.mark.asyncio
async def test_recent_creations_paginates_and_deduplicates(ctx):
    calls: list[dict] = []

    class Response:
        status_code = 200

        def __init__(self, records):
            self._records = records

        def json(self):
            return {"data": self._records}

    async def fake_get(url, *, headers, params, timeout):
        calls.append(params)
        if params["page"] == 1:
            return Response([{"creation": {"url": "https://cdn.example/one.png"}}] * 100)
        return Response([{"creation": {"url": "https://cdn.example/two.png"}}])

    ctx.http.get = fake_get
    urls = await recovery.list_recent_creation_urls(ctx, "key")

    assert urls == ["https://cdn.example/one.png", "https://cdn.example/two.png"]
    assert [call["page"] for call in calls] == [1, 2]

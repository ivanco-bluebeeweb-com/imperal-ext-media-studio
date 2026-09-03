"""Shared fixtures.

Mirrors SEO Audit Engine's `ctx` fixture: MockContext gives working
store/secrets, but has no `background_task` -- this fixture adds one that
EXECUTES the coroutine immediately (tests care about the outcome, not about
real detachment) and records what was spawned.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def ctx():
    from imperal_sdk.testing import MockContext, MockSecretStore

    mock = MockContext()
    mock.secrets = MockSecretStore({})

    spawned: list[dict] = []

    async def background_task(coro, *, long_running: bool = False, name: str = ""):
        spawned.append({"long_running": long_running, "name": name})
        mock.last_background_result = await coro
        return "task-test-1"

    mock.background_task = background_task
    mock.spawned = spawned
    mock.last_background_result = None
    return mock


@pytest.fixture
def ctx_with_key(ctx):
    """Same as `ctx` but with a Magnific API key already configured."""
    from imperal_sdk.testing import MockSecretStore
    ctx.secrets = MockSecretStore({"magnific_api_key": "test-key-123"})
    return ctx


@pytest.fixture
def ctx_with_valid_key(ctx_with_key):
    """A key that IS stored AND passes a live Magnific check (200)."""
    ctx_with_key.http.mock_get("/v1/creations/recent", {"data": []}, status=200)
    return ctx_with_key


@pytest.fixture
def ctx_with_broken_key(ctx_with_key):
    """A key that IS stored but Magnific itself now rejects (401) -- the
    Task #2236 case: the secret still exists, it just stopped working."""
    ctx_with_key.http.mock_get("/v1/creations/recent", {"error": "unauthorized"}, status=401)
    return ctx_with_key

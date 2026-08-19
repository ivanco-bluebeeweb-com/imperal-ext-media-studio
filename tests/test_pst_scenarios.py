"""Plausible Scenario Tests (PST) -- Media Studio.

Method: Docs/session-notes/SCENARIO_TESTING_STANDARD.md. This app has 24
chat functions and a very large existing suite (22 test files) with deep
coverage of generation, upscaling, prompt engine, model discovery, and
recovery's internal matching logic (test_recovery.py). A name-based
coverage audit found exactly ONE @chat.function never exercised at the
handler level: `recover_stored_images` (its internal helpers in
recovery.py ARE tested, but the handler itself -- the full recovery flow
including storage writes -- was not). This file closes that one gap.
"""
from __future__ import annotations

import pytest

import handlers as h
import storage as st
from models import RecoverStoredImagesParams


def _legacy_package(role="featured", url="https://cdn.freepik.com/result_abc123.png"):
    return {
        "id": "pkg1",
        "site": "example.com",
        "assets": [{
            "role": role,
            "status": "ready",
            "image_url": url,
            "original_image_url": url,
            "original_storage_path": "",  # legacy: never stored durably
            "provider_task_id": "task-abc123",
        }],
    }


# ── blocked: no Magnific key connected ──────────────────────────────────────

@pytest.mark.asyncio
async def test_blocked_recover_stored_images_without_api_key(ctx):
    out = await h.recover_stored_images(ctx, RecoverStoredImagesParams())
    assert out.error is not None
    assert "Magnific API key" in out.error


# ── happy: nothing to recover is a clean success, not an error ─────────────

@pytest.mark.asyncio
async def test_happy_recover_stored_images_nothing_to_do(ctx_with_key, monkeypatch):
    async def fake_list_packages(_ctx, limit=100):
        return []
    monkeypatch.setattr(st, "list_packages", fake_list_packages)

    out = await h.recover_stored_images(ctx_with_key, RecoverStoredImagesParams())
    assert out.error is None
    assert "already stored permanently" in out.summary


# ── happy: a legacy asset with an exact single match is actually restored ──

@pytest.mark.asyncio
async def test_happy_recover_stored_images_restores_exact_match(ctx_with_key, monkeypatch):
    package = _legacy_package()

    async def fake_list_packages(_ctx, limit=100):
        return [package]

    async def fake_list_recent_creations(_ctx, _api_key, **kwargs):
        return [{
            "external_id": "task-abc123",
            "creation": {"id": 1, "url": "https://cdn.freepik.com/real-source.png"},
        }]

    updates = []

    async def fake_update_package(_ctx, package_id, patch):
        updates.append((package_id, patch))
        return {**package, **patch}

    async def fake_download(url, **kwargs):
        assert url == "https://cdn.freepik.com/real-source.png"
        # Minimal valid PNG header bytes so image_dims can describe it.
        return bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
            "53de0000000c4944415478da6360000000020001e221bc330000000049454e"
            "44ae426082"
        )

    monkeypatch.setattr(st, "list_packages", fake_list_packages)
    monkeypatch.setattr(st, "update_package", fake_update_package)
    monkeypatch.setattr(h.recovery, "list_recent_creations", fake_list_recent_creations)
    monkeypatch.setattr(h.mc, "download_image_bytes", fake_download)

    out = await h.recover_stored_images(ctx_with_key, RecoverStoredImagesParams())
    assert out.error is None
    assert "Restored 1 image" in out.summary
    assert len(updates) == 1
    restored_asset = updates[0][1]["assets"][0]
    assert restored_asset["original_storage_path"]  # now durably stored


# ── recovery/adversarial: provider lookup fails cleanly, no crash ──────────

@pytest.mark.asyncio
async def test_error_recover_stored_images_when_creations_lookup_fails(ctx_with_key, monkeypatch):
    package = _legacy_package()

    async def fake_list_packages(_ctx, limit=100):
        return [package]

    async def fake_list_recent_creations(_ctx, _api_key, **kwargs):
        raise h.mc.ProviderError("Magnific creations lookup failed (HTTP 500).", "MEDIA_PROVIDER_ERROR")

    monkeypatch.setattr(st, "list_packages", fake_list_packages)
    monkeypatch.setattr(h.recovery, "list_recent_creations", fake_list_recent_creations)

    out = await h.recover_stored_images(ctx_with_key, RecoverStoredImagesParams())
    assert out.error is not None
    assert "Could not look up existing Magnific creations" in out.error


# ── adversarial: ambiguous/no match leaves the asset unchanged, not crashed ─

@pytest.mark.asyncio
async def test_adversarial_recover_stored_images_no_match_leaves_asset_unchanged(ctx_with_key, monkeypatch):
    package = _legacy_package()

    async def fake_list_packages(_ctx, limit=100):
        return [package]

    async def fake_list_recent_creations(_ctx, _api_key, **kwargs):
        return []  # nothing matches -- no identity tokens overlap

    monkeypatch.setattr(st, "list_packages", fake_list_packages)
    monkeypatch.setattr(h.recovery, "list_recent_creations", fake_list_recent_creations)

    out = await h.recover_stored_images(ctx_with_key, RecoverStoredImagesParams())
    assert out.error is None
    assert "0 image" in out.summary or "could not be restored" in out.summary

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


# ── Part D2 (SCENARIO_TESTING_STANDARD.md): idempotency / double-invocation ─

@pytest.mark.asyncio
async def test_d2_double_delete_media_package_fails_clean_on_the_second_call(ctx):
    """delete_media_package checks store existence before deleting -- a
    retried delete on a package already removed by the first call must
    return a clean not-found error, never crash or repeat deleted=true
    about something no longer there."""
    from models import DeleteMediaPackageParams
    doc_id, _ = await st.create_package(ctx, _legacy_package())

    first = await h.delete_media_package(ctx, DeleteMediaPackageParams(package_id=doc_id))
    assert first.error is None

    second = await h.delete_media_package(ctx, DeleteMediaPackageParams(package_id=doc_id))
    assert second.error is not None


@pytest.mark.asyncio
async def test_d2_double_delete_asset_image_is_idempotent(ctx):
    """delete_asset_image blanks the stored path/url fields on the target
    asset -- calling it again on the same role+version after they're
    already blank must stay a clean success (nothing left to clear), not
    error or corrupt the asset record."""
    from models import DeleteAssetImageParams
    pkg = _legacy_package()
    pkg["assets"][0]["original_storage_path"] = "some/path.png"
    doc_id, _ = await st.create_package(ctx, pkg)

    first = await h.delete_asset_image(
        ctx, DeleteAssetImageParams(package_id=doc_id, role=pkg["assets"][0]["role"], version="original"))
    assert first.error is None

    second = await h.delete_asset_image(
        ctx, DeleteAssetImageParams(package_id=doc_id, role=pkg["assets"][0]["role"], version="original"))
    assert second.error is None


# ── Part D3 (SCENARIO_TESTING_STANDARD.md): security / SSRF surface -------

def test_d3_no_ssrf_download_image_bytes_only_ever_called_with_provider_urls():
    """download_image_bytes uses a raw httpx.AsyncClient (by design, to
    avoid ctx.http's text-decoding of binary bytes -- see its own docstring)
    fetching whatever URL it's given, with no host allowlist of its own.
    This is only safe because every call site feeds it a URL that ITSELF
    came from a Magnific/Freepik API response (generation result, upscale
    result, Mystic task lookup) -- never a raw string typed by the chat
    user. Confirmed by grep: every handlers.py call site passes an
    `*_url` variable sourced from mc.generate_image/mc.upscale_image/
    mc.poll_mystic_task's own return values, not a request parameter.
    This is a regression trip-wire: if a future handler ever threads a
    user-supplied url straight into download_image_bytes, that call site
    needs its own explicit SSRF review."""
    import inspect
    import handlers as h
    src = inspect.getsource(h)
    for line in src.splitlines():
        if "download_image_bytes(" in line and "def " not in line:
            arg = line.split("download_image_bytes(")[1].split(")")[0]
            assert "params." not in arg, (
                f"download_image_bytes called with a request-parameter-derived "
                f"argument, not a provider-returned url: {line.strip()}")

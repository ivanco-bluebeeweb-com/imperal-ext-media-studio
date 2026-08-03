import pytest

import storage as st


@pytest.mark.asyncio
async def test_create_returns_store_minted_id(ctx):
    package_id, row = await st.create_package(ctx, {"site": "g4s.md", "status": "draft"})
    assert package_id
    assert row["site"] == "g4s.md"
    assert "created_at" in row and "updated_at" in row


@pytest.mark.asyncio
async def test_get_roundtrip_includes_id(ctx):
    package_id, _ = await st.create_package(ctx, {"site": "g4s.md"})
    fetched = await st.get_package(ctx, package_id)
    assert fetched["id"] == package_id
    assert fetched["site"] == "g4s.md"


@pytest.mark.asyncio
async def test_get_missing_returns_none(ctx):
    assert await st.get_package(ctx, "does-not-exist") is None


@pytest.mark.asyncio
async def test_update_merges_and_bumps_updated_at(ctx):
    package_id, row = await st.create_package(ctx, {"site": "g4s.md", "status": "draft"})
    merged = await st.update_package(ctx, package_id, {"status": "generating"})
    assert merged["status"] == "generating"
    assert merged["site"] == "g4s.md"
    assert merged["id"] == package_id
    assert merged["updated_at"] >= row["updated_at"]


@pytest.mark.asyncio
async def test_update_missing_returns_none(ctx):
    assert await st.update_package(ctx, "nope", {"status": "x"}) is None


@pytest.mark.asyncio
async def test_delete_existing_returns_true_then_gone(ctx):
    package_id, _ = await st.create_package(ctx, {"site": "g4s.md"})
    assert await st.delete_package(ctx, package_id) is True
    assert await st.get_package(ctx, package_id) is None


@pytest.mark.asyncio
async def test_delete_missing_returns_false(ctx):
    assert await st.delete_package(ctx, "nope") is False


@pytest.mark.asyncio
async def test_list_filters_by_site_and_status(ctx):
    await st.create_package(ctx, {"site": "g4s.md", "status": "draft"})
    await st.create_package(ctx, {"site": "g4s.md", "status": "ready"})
    await st.create_package(ctx, {"site": "climtec.md", "status": "draft"})

    all_rows = await st.list_packages(ctx)
    assert len(all_rows) == 3

    g4s_rows = await st.list_packages(ctx, site="g4s.md")
    assert len(g4s_rows) == 2

    g4s_draft = await st.list_packages(ctx, site="g4s.md", status="draft")
    assert len(g4s_draft) == 1
    assert g4s_draft[0]["status"] == "draft"

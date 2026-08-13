"""Tests for the register_project IPC surface Sites Registry calls into
whenever a site is registered there, per the platform-wide rule: a site
added to Sites Registry or WordPress Hub must show up as an existing
project here without the user re-adding it by hand.
"""
import pytest

import handlers as h


@pytest.mark.asyncio
async def test_register_project_creates_a_new_project(ctx):
    result = await h.expose_register_project(ctx, site_id="g4s.md", domain="g4s.md", name="G4S")
    assert result == {"ok": True, "site_id": "g4s.md", "created": True}

    listed = await h.st.list_projects(ctx)
    assert any(p["site_id"] == "g4s.md" for p in listed)


@pytest.mark.asyncio
async def test_register_project_is_idempotent(ctx):
    await h.expose_register_project(ctx, site_id="g4s.md", domain="g4s.md", name="G4S")
    result = await h.expose_register_project(ctx, site_id="g4s.md", domain="g4s.md", name="G4S")
    assert result == {"ok": True, "site_id": "g4s.md", "created": False}

    listed = await h.st.list_projects(ctx)
    assert sum(1 for p in listed if p["site_id"] == "g4s.md") == 1


@pytest.mark.asyncio
async def test_register_project_falls_back_to_domain_when_site_id_missing(ctx):
    result = await h.expose_register_project(ctx, domain="climtec.md", name="Climtec")
    assert result["ok"] is True
    assert result["site_id"] == "climtec.md"


@pytest.mark.asyncio
async def test_register_project_requires_site_id_or_domain(ctx):
    result = await h.expose_register_project(ctx)
    assert result["ok"] is False

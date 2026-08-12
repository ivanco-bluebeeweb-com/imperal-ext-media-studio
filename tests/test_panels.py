"""One 'App settings' button, one settings screen -- UI_INTERFACE_STANDARD.md.

WHY THIS EXISTS. `panels.py` used to split provider management across two
center-slot screens ("connect" and "providers"), and the daily model-discovery
check had NO panel surface at all -- only chat tools. That is exactly the
class of bug UI_INTERFACE_STANDARD.md calls out: settings scattered instead
of gathered in one place. This test locks in the fix so it can't quietly
regress back to two screens or a hidden discovery feature.
"""

from __future__ import annotations

import pytest

import panels


@pytest.mark.asyncio
async def test_sidebar_has_exactly_one_app_settings_button(ctx):
    """UI_INTERFACE_STANDARD.md rule 1: exactly one 'App settings' button."""
    node = await panels.packages_nav_panel(ctx)
    rendered = repr(node)
    assert rendered.count("App settings") == 1
    assert "Manage providers" not in rendered


@pytest.mark.asyncio
async def test_settings_view_is_reachable_and_replaces_center_slot(ctx):
    """Clicking it must render in the SAME center panel (studio), not a
    separate one -- confirmed by calling studio_panel(view='settings')
    directly, the same call the sidebar button issues."""
    node = await panels.studio_panel(ctx, view="settings")
    rendered = repr(node)
    assert "App settings" in rendered


@pytest.mark.asyncio
async def test_settings_screen_covers_the_provider_key(ctx):
    """The provider connect/disconnect form must live inside settings, not
    on a screen of its own."""
    node = await panels.studio_panel(ctx, view="settings")
    rendered = repr(node)
    assert "Image provider" in rendered
    assert "connect_magnific" in rendered


@pytest.mark.asyncio
async def test_settings_screen_covers_the_webhook_secret_honestly(ctx):
    """The webhook secret is declared in app.py but read by no handler yet
    -- the settings screen must say so, not silently omit it or claim it
    does something it doesn't."""
    node = await panels.studio_panel(ctx, view="settings")
    rendered = repr(node)
    assert "Webhook secret" in rendered
    assert "checks image status itself" in rendered


@pytest.mark.asyncio
async def test_settings_screen_surfaces_model_discovery_previously_chat_only(ctx):
    """check_new_models / list_model_discovery_log had no panel surface
    before this screen -- a non-chat user could never see or trigger them.
    Both must now be reachable from App settings."""
    node = await panels.studio_panel(ctx, view="settings")
    rendered = repr(node)
    assert "New model checks" in rendered
    assert "check_new_models" in rendered


@pytest.mark.asyncio
async def test_old_connect_and_providers_views_still_resolve_to_settings(ctx):
    """Old ui.Call(view='connect') / view='providers') call sites (if any
    survive in a cached client) must land somewhere sensible, not a blank
    or broken screen."""
    connect_node = await panels.studio_panel(ctx, view="connect")
    providers_node = await panels.studio_panel(ctx, view="providers")
    settings_node = await panels.studio_panel(ctx, view="settings")
    assert repr(connect_node) == repr(settings_node)
    assert repr(providers_node) == repr(settings_node)


@pytest.mark.asyncio
async def test_default_view_is_the_central_brief_catalog_even_without_provider(ctx):
    """The main workspace is always the brief catalogue. A disconnected
    provider is an inline warning, not a detour into settings."""
    node = await panels.studio_panel(ctx)
    rendered = repr(node)
    assert "Media briefs" in rendered
    assert "Connect a provider to generate images" in rendered
    assert "Image provider" not in rendered


@pytest.mark.asyncio
async def test_central_brief_catalog_contains_search_and_new_brief(ctx_with_key):
    """Browsing is on-page: not hidden in the left sidebar or replaced by
    the former 'Pick a media package on the left' empty state."""
    node = await panels.studio_panel(ctx_with_key)
    rendered = repr(node)
    assert "Media briefs" in rendered
    assert "New brief" in rendered
    assert "Pick a media package on the left" not in rendered


@pytest.mark.asyncio
async def test_central_brief_catalog_makes_existing_briefs_searchable(ctx_with_key, monkeypatch):
    async def fake_packages(_ctx, *, limit):
        return [{
            "id": "brief-1", "article_title": "Heat recovery guide",
            "site": "example.com", "status": "ready",
            "assets": [{"status": "ready"}],
        }]

    monkeypatch.setattr(panels.st, "list_packages", fake_packages)
    node = await panels._packages_view(ctx_with_key, any_connected=True)
    rendered = repr(node)
    assert "Heat recovery guide" in rendered
    assert "'searchable': True" in rendered


@pytest.mark.asyncio
async def test_sidebar_is_settings_only_not_a_second_brief_catalog(ctx_with_key):
    node = await panels.packages_nav_panel(ctx_with_key)
    rendered = repr(node)
    assert "App settings" in rendered
    assert "New brief" not in rendered
    assert "searchable=True" not in rendered

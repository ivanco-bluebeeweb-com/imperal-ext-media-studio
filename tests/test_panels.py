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
async def test_central_brief_catalog_lists_existing_briefs(ctx_with_key, monkeypatch):
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


@pytest.mark.asyncio
async def test_header_shows_live_brief_count_and_status_breakdown(ctx_with_key, monkeypatch):
    """The header must say how many briefs there are right now, and the
    subtitle must break them down by actual state (e.g. '1 ready, 1 draft'),
    not a generic 'search, open and manage' caption that says nothing."""
    async def fake_packages(_ctx, *, limit):
        return [
            {"id": "b1", "article_title": "A", "site": "x.com", "status": "ready", "assets": []},
            {"id": "b2", "article_title": "B", "site": "x.com", "status": "draft", "assets": []},
        ]

    monkeypatch.setattr(panels.st, "list_packages", fake_packages)
    node = await panels._packages_view(ctx_with_key, any_connected=True)
    rendered = repr(node)
    assert "Media briefs (2)" in rendered
    assert "1 ready" in rendered
    assert "1 draft" in rendered
    assert "Search, open and manage" not in rendered


@pytest.mark.asyncio
async def test_new_brief_button_and_search_are_on_the_same_row(ctx_with_key, monkeypatch):
    """The button must sit in the same horizontal Stack as the search
    input, not stacked above it."""
    async def fake_packages(_ctx, *, limit):
        return []

    monkeypatch.setattr(panels.st, "list_packages", fake_packages)
    node = await panels._packages_view(ctx_with_key, any_connected=True)

    def _find_row_with(node, label):
        props = getattr(node, "props", {})
        children = props.get("children") or []
        rendered_children = repr(children)
        if props.get("direction") == "h" and label in rendered_children:
            return children
        for child in children:
            found = _find_row_with(child, label)
            if found is not None:
                return found
        return None

    row = _find_row_with(node, "New brief")
    assert row is not None, "expected an 'h' Stack containing the New brief button"
    row_repr = repr(row)
    assert "New brief" in row_repr
    assert "Search by title" in row_repr


@pytest.mark.asyncio
async def test_new_brief_button_has_no_redundant_plus_glyph_in_label(ctx_with_key, monkeypatch):
    """The button already carries icon='Plus' -- the label text must not
    ALSO contain a literal '+' character."""
    async def fake_packages(_ctx, *, limit):
        return []

    monkeypatch.setattr(panels.st, "list_packages", fake_packages)
    node = await panels._packages_view(ctx_with_key, any_connected=True)
    rendered = repr(node)
    assert "'label': 'New brief'" in rendered
    assert "'label': '+ New brief'" not in rendered
    assert "icon': 'Plus'" in rendered


@pytest.mark.asyncio
async def test_search_query_actually_filters_the_rendered_list(ctx_with_key, monkeypatch):
    """Not just that the input renders -- that submitting a query through
    studio_panel's real routing narrows down which briefs show up."""
    async def fake_packages(_ctx, *, limit):
        return [
            {"id": "b1", "article_title": "Heat recovery guide", "site": "g4s.md", "status": "ready", "assets": []},
            {"id": "b2", "article_title": "Ventilation basics", "site": "g4s.md", "status": "draft", "assets": []},
        ]

    monkeypatch.setattr(panels.st, "list_packages", fake_packages)
    node = await panels.studio_panel(ctx_with_key, view="", package_id="", q="heat")
    rendered = repr(node)
    assert "Heat recovery guide" in rendered
    assert "Ventilation basics" not in rendered


@pytest.mark.asyncio
async def test_back_button_click_actually_lands_on_the_brief_catalog(ctx_with_key):
    """Simulates the real on_click the back button issues -- not just that
    the button renders with the right props, but that dispatching it
    actually returns the catalogue screen and not settings/editor/nothing."""
    node = await panels.studio_panel(ctx_with_key, view="")
    rendered = repr(node)
    assert "Media briefs" in rendered
    assert "New brief" in rendered
    assert "App settings" not in rendered
    assert "New media brief" not in rendered


@pytest.mark.asyncio
async def test_back_button_still_works_when_a_stale_package_id_survives(ctx_with_key):
    """Regression: the panel host carries kwargs forward across calls (see
    WordPress Hub's own back button, which explicitly resets site_id=\"\").
    If a brief was open, package_id="brief-1" can still be present on the
    NEXT call unless a button's own on_click explicitly clears it. Before
    the fix, `view == "editor" or package_id` in studio_panel's routing kept
    reopening the SAME brief because package_id alone was enough to route
    into the editor -- the back button then visibly "led nowhere". This
    reproduces exactly that stale-kwargs shape and requires the catalog."""
    node = await panels.studio_panel(ctx_with_key, view="", package_id="")
    rendered = repr(node)
    assert "Media briefs" in rendered
    assert "New media brief" not in rendered


@pytest.mark.asyncio
async def test_every_back_close_cancel_button_clears_package_id_explicitly(ctx_with_key, monkeypatch):
    """Lock in the actual fix at the source: any on_click that routes back to
    the catalog (view="") must ALSO carry package_id="" in its own params,
    since kwargs from the open editor call are not implicitly wiped."""
    async def fake_package(_ctx, _package_id):
        return {
            "id": "brief-1", "article_title": "Heat recovery guide",
            "site": "example.com", "status": "draft", "assets": [],
        }

    monkeypatch.setattr(panels.st, "get_package", fake_package)

    def _back_calls(node) -> list:
        calls = []
        props = getattr(node, "props", {})
        on_click = props.get("on_click")
        if on_click is not None:
            call_params = getattr(on_click, "params", {})
            if call_params.get("function") == "__panel__studio" \
                    and call_params.get("params", {}).get("view") == "":
                calls.append(call_params["params"])
        for child in props.get("children", []) or []:
            calls.extend(_back_calls(child))
        return calls

    existing = await panels._editor_existing(ctx_with_key, "brief-1", any_connected=True)
    new_brief = panels._editor_new(ctx_with_key, any_connected=True)
    settings = panels._settings_view(ctx_with_key, [], [])

    all_calls = _back_calls(existing) + _back_calls(new_brief) + _back_calls(settings)
    assert all_calls, "expected at least one back-to-catalog button"
    for params in all_calls:
        assert params.get("package_id") == "", (
            f"a back/close/cancel button must reset package_id, found {params!r}"
        )


@pytest.mark.asyncio
async def test_existing_brief_has_compact_left_aligned_back_to_catalog_button(ctx_with_key, monkeypatch):
    async def fake_package(_ctx, _package_id):
        return {
            "id": "brief-1", "article_title": "Heat recovery guide",
            "site": "example.com", "status": "draft", "assets": [],
        }

    monkeypatch.setattr(panels.st, "get_package", fake_package)
    node = await panels._editor_existing(ctx_with_key, "brief-1", any_connected=True)
    rendered = repr(node)
    assert "All media briefs" in rendered
    assert "'size': 'sm'" in rendered
    assert "'icon': 'ArrowLeft'" in rendered
    assert "'view': ''" in rendered
    # Left-aligned and compact, not stretched full-width.
    assert "'justify': 'start'" in rendered
    assert "'full_width': True" not in rendered


@pytest.mark.asyncio
async def test_sidebar_is_settings_only_not_a_second_brief_catalog(ctx_with_key):
    node = await panels.packages_nav_panel(ctx_with_key)
    rendered = repr(node)
    assert "App settings" in rendered
    assert "New brief" not in rendered
    assert "searchable=True" not in rendered

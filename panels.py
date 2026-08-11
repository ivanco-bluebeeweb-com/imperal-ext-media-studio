"""Panel UI: left nav + ONE center panel, screens selected by `view`.

WHY ONE CENTER PANEL, SCREENS AS A PARAMETER (same pattern as Asana/Notion
Connector -- see their panels.py for the fuller writeup). A center slot holds
exactly ONE panel with REPLACE semantics: two panels both claiming
`slot="center"` race at session-init batch discovery, and pressing a button
that dispatches the loser looks like nothing happening. So there is exactly
one owner, `studio`, and `view` picks the screen:

    ui.Call("__panel__studio")                     -> packages (default)
    ui.Call("__panel__studio", view="settings")     -> App settings screen
    ui.Call("__panel__studio", view="editor", package_id=...) -> package editor

WHY ONE "App settings" SCREEN, NOT TWO ("connect" + "providers").
UI_INTERFACE_STANDARD.md requires exactly one "App settings" button in the
sidebar, replacing the center slot, gathering EVERYTHING configurable in one
place. Splitting provider-connect and provider-manage across two screens was
fine back when there was only a key to paste, but it also meant the daily
model-discovery check (check_new_models / list_model_discovery_log) had NO
panel surface at all -- chat-only, invisible to a user who never opens chat.
`settings` replaces both old screens and folds in that discovery section too.
`connect`/`providers` are still accepted as view aliases so nothing that
already links to them breaks.

The secret is `write_mode="both"` (see app.py) and `providers.py` validates
a pasted key against Magnific before saving it, so the settings screen can
show live connect/disconnect status instead of guessing.

Every component below is used strictly per its real signature in
`imperal_sdk.ui` (checked in source, not guessed) -- e.g. `ui.List` takes
`items=`, not `children=`; `ui.Form` has no `defaults=` string labels, only
`param_name`-bearing inputs; `ActionResult.success(data, summary)` is
positional, there is no `.ok()`.
"""

from __future__ import annotations

from imperal_sdk import ui

from app import ext
import model_discovery as md
import magnific_client as mc
import storage as st
import model_registry as mr
from providers import list_provider_connections
from shared import MYSTIC_MODELS, is_image_url_expired

_MODEL_OPTIONS = (
    [{"value": "", "label": "Mystic default"},
     {"value": "auto", "label": "Auto (Media Hub picks the best model)"}]
    + [{"value": m, "label": m.replace("_", " ")} for m in MYSTIC_MODELS]
    + [{"value": m_id, "label": spec.label} for m_id, spec in mr.MODELS.items()
       if m_id != "mystic"]
)

_MAGNIFIC_SIGNUP_URL = "https://www.magnific.com/api"


_STATUS_COLOR = {
    "draft": "gray",
    "generating": "blue",
    "ready": "green",
    "partial": "yellow",
    "failed": "red",
}


def _status_badge(status: str) -> ui.UINode:
    return ui.Badge(label=status or "draft", color=_STATUS_COLOR.get(status, "gray"))


def _asset_progress(assets: list[dict]) -> str:
    if not assets:
        return "no assets"
    ready = sum(1 for a in assets if a.get("status") == "ready")
    return f"{ready}/{len(assets)} ready"


# ── Left sidebar ──────────────────────────────────────────────────────────────

@ext.panel(
    "packages_nav",
    slot="left",
    title="Media Hub",
    default_width=300,
    min_width=220,
    max_width=420,
    refresh="on_event:media-studio.create_media_brief,media-studio.generate_media_package,"
            "media-studio.delete_media_package,media-studio.connect_magnific,"
            "media-studio.disconnect_magnific",
)
async def packages_nav_panel(ctx) -> ui.UINode:
    """Package list PLUS a permanent connection-status row.

    The status row is the fix for "I don't understand how to connect
    Magnific from the interface": it is visible every time this panel
    renders, not something the user has to already know to look for, and it
    is the same row whether zero, one, or (later) several providers exist.
    """
    connections = await list_provider_connections(ctx)
    any_connected = any(c.connected for c in connections)

    status_row = ui.Card(
        title="Providers",
        subtitle=(
            "Magnific connected" if any_connected
            else "No provider connected yet"
        ),
        content=ui.Stack(children=[
            ui.Button(
                "App settings", icon="Settings", variant="secondary", size="sm",
                on_click=ui.Call("__panel__studio", view="settings"),
            ),
        ], direction="h"),
    )

    header = ui.Stack(children=[
        ui.Button(
            "+ New brief", icon="Plus", variant="primary",
            disabled=not any_connected,
            on_click=ui.Call("__panel__studio", view="editor", package_id="new"),
        ),
    ], direction="h", justify="end")

    children: list[ui.UINode] = [status_row]

    if not any_connected:
        children.append(ui.Alert(
            title="Connect a provider to generate images",
            message="Media packages can be drafted without a provider, but "
                     "generating images needs Magnific connected first.",
            type="warning",
        ))

    rows = await st.list_packages(ctx, limit=100)
    children.append(header)

    if not rows:
        children.append(ui.Empty(
            message="No media packages yet -- create a brief to generate a "
                    "featured image plus inline images.",
        ))
    else:
        items = [
            ui.ListItem(
                id=r["id"],
                title=r.get("article_title") or "(untitled brief)",
                subtitle=r.get("site", ""),
                meta=_asset_progress(r.get("assets", [])),
                badge=_status_badge(r.get("status", "draft")),
                on_click=ui.Call("__panel__studio", view="editor", package_id=r["id"]),
                actions=[{
                    "icon": "Trash2",
                    "on_click": ui.Call("delete_media_package", package_id=r["id"]),
                    "confirm": f"Delete media package '{r.get('article_title') or r['id']}'?",
                }],
            )
            for r in rows
        ]
        children.append(ui.List(items=items, searchable=True))

    return ui.Stack(children=children, gap=3)


# ── App settings screen -- EVERYTHING configurable, one place ──────────────
#
# UI_INTERFACE_STANDARD.md rule 1-3: exactly one "App settings" button, it
# replaces the center slot, and it gathers every configurable thing --
# not just the provider key. Before this, "Connect" and "Providers" were two
# separate screens (fine when there was only a key to manage), and the daily
# model-discovery check (check_new_models / list_model_discovery_log) was a
# chat-only tool with NO panel surface at all -- a user who doesn't use chat
# could never see whether new models had been found. This screen replaces
# both old screens and adds the missing discovery section.

def _settings_view(ctx, connections: list, log: list[dict]) -> ui.UINode:
    magnific = next((c for c in connections if c.provider == "magnific"), None)
    connected = bool(magnific and magnific.connected)

    children: list[ui.UINode] = [
        ui.Header(text="App settings", level=2,
                   subtitle="Everything you can configure in Media Hub"),
    ]

    # -- Image provider --------------------------------------------------
    provider_children: list[ui.UINode] = []
    if connected:
        provider_children.append(ui.Alert(
            title="Magnific connected", message="Images are ready to generate.",
            type="success",
        ))
    else:
        provider_children.append(ui.Alert(
            title="Not connected",
            message="Paste an API key below to start generating images.",
            type="info",
        ))
        provider_children.append(ui.Link(
            label="Get a key on magnific.com", href=_MAGNIFIC_SIGNUP_URL,
        ))

    provider_children.append(ui.Form(
        action="connect_magnific",
        submit_label="Verify and connect",
        children=[
            ui.Password(param_name="api_key", placeholder="Magnific API key"),
        ],
    ))
    if connected:
        provider_children.append(ui.Button(
            "Disconnect", icon="Unlink", variant="danger", size="sm",
            on_click=ui.Call("disconnect_magnific"),
        ))
    children.append(ui.Section(title="Image provider", children=provider_children))

    # -- Webhook secret (declared, not used yet -- say so honestly) -----
    children.append(ui.Section(
        title="Webhook secret",
        children=[
            ui.Text(
                content="Not needed yet -- Media Hub checks image status "
                        "itself, without webhooks.",
                variant="caption",
            ),
        ],
    ))

    # -- New model checks (was chat-only; now visible here too) ----------
    discovery_children: list[ui.UINode] = [
        ui.Text(
            content="Media Hub checks once a day for new image models. "
                    "It only reports what it finds -- it never turns that "
                    "on by itself.",
            variant="caption",
        ),
        ui.Button(
            "Check now", icon="RefreshCw", variant="secondary",
            size="sm", on_click=ui.Call("check_new_models"),
        ),
    ]
    if log:
        items = [
            ui.ListItem(
                id=entry.get("date", ""),
                title=entry.get("date", ""),
                subtitle=(
                    f"{len(entry.get('found') or [])} new candidate(s)"
                    if entry.get("found") else "nothing new"
                ),
                meta=entry.get("result", ""),
            )
            for entry in log
        ]
        discovery_children.append(ui.List(items=items))
    else:
        discovery_children.append(ui.Empty(
            message="No checks yet -- click Check now above.",
        ))
    children.append(ui.Section(title="New model checks", children=discovery_children))

    children.append(ui.Button(
        "Close", variant="ghost",
        on_click=ui.Call("__panel__studio", view=""),
    ))

    return ui.Stack(children=children, gap=4)


# ── Package editor screen ────────────────────────────────────────────────────

def _asset_card(package_id: str, asset: dict) -> ui.UINode:
    role = asset.get("role", "")
    status = asset.get("status", "pending")
    image_children: list[ui.UINode] = []
    url_expired = status == "ready" and is_image_url_expired(asset.get("image_url", ""))

    # The original is a distinct deliverable: never hide it behind its upscale.
    original_url = asset.get("original_image_url") or asset.get("image_url", "")
    if original_url and not url_expired:
        image_children.extend([
            ui.Text("Original image", variant="label"),
            ui.Image(src=original_url, alt=asset.get("alt_text", ""), width="100%", object_fit="cover"),
            ui.Text(
                " · ".join(part for part in (
                    asset.get("original_dimensions", ""), asset.get("original_format", ""),
                    asset.get("original_file_size", ""),
                ) if part) or "Size, format and file weight unavailable",
                variant="caption",
            ),
        ])
        if asset.get("upscaled_image_url"):
            image_children.extend([
                ui.Text("Upscaled image", variant="label"),
                ui.Image(src=asset["upscaled_image_url"], alt=asset.get("alt_text", ""), width="100%", object_fit="cover"),
                ui.Text(
                    " · ".join(part for part in (
                        asset.get("upscaled_dimensions", ""), asset.get("upscaled_format", ""),
                        asset.get("upscaled_file_size", ""),
                    ) if part) or "Size, format and file weight unavailable",
                    variant="caption",
                ),
            ])
    elif url_expired:
        image_children.append(ui.Alert(
            title="Image link expired",
            message="This image was generated earlier and its hosted link has expired. Open Regenerate below to get a fresh one.",
            type="warning",
        ))
    elif status == "generating":
        image_children.append(ui.Loading(message="Generating..."))
    elif status == "failed":
        image_children.append(ui.Alert(message=asset.get("error", "Generation failed."), type="error"))
    else:
        image_children.append(ui.Text("Not generated yet.", variant="caption"))

    image_title = asset.get("filename") or _asset_title(role)
    upscale_children: list[ui.UINode] = []
    if original_url and not url_expired:
        upscale_children.append(ui.Form(
            action="generate_asset_upscale",
            submit_label="Generate Upscale",
            defaults={"package_id": package_id, "role": role},
            children=[
                ui.Text("Increase size", variant="label"),
                ui.Select(
                    param_name="scale_factor",
                    options=[{"value": factor, "label": factor} for factor in mc.available_upscale_scale_factors()],
                    value="2x",
                ),
            ],
        ))
    else:
        upscale_children.append(ui.Text("Generate or regenerate the image before upscaling it.", variant="caption"))

    metadata_children: list[ui.UINode] = [ui.Form(
        action="update_asset_meta",
        submit_label="Save Changes",
        defaults={"package_id": package_id, "role": role},
        children=[
            ui.Text("Image title", variant="label"),
            ui.Input(param_name="image_title", placeholder="A clear file title", value=image_title),
            ui.Text("Image description", variant="label"),
            ui.TextArea(
                param_name="image_description",
                placeholder="Describe the image to generate, in English",
                value=asset.get("prompt", ""),
            ),
            ui.Text("Alt text", variant="label"),
            ui.Input(param_name="alt_text", placeholder="Describe the image for screen readers", value=asset.get("alt_text", "")),
            ui.Text("Caption", variant="label"),
            ui.Input(param_name="caption", placeholder="Short visible caption (optional)", value=asset.get("caption", "")),
        ],
    )]

    regenerate_children = [ui.Form(
        action="regenerate_asset",
        submit_label="Regenerate",
        defaults={"package_id": package_id, "role": role},
        children=[
            ui.Text("Model", variant="label"),
            ui.Select(param_name="model", options=_MODEL_OPTIONS, value=asset.get("model", ""), placeholder="Choose a model"),
        ],
    )]
    image_children.append(ui.Accordion(sections=[
        {"id": "upscaling", "title": "Upscaling", "children": upscale_children},
        {"id": "metadata", "title": "Metadata", "children": metadata_children},
        {"id": "regenerate", "title": "Regenerate", "children": regenerate_children},
    ]))

    role_title = (role or "image").replace("_", " ").title()
    image_children.insert(0, ui.Row(
        children=[
            ui.Text(f"{role_title} Image", variant="label"),
            _status_badge(status),
        ],
        gap=2,
    ))
    return ui.Card(
        content=ui.Stack(children=image_children, gap=2),
    )


def _editor_new(ctx, any_connected: bool) -> ui.UINode:
    children: list[ui.UINode] = [ui.Header(text="New media brief", level=3)]

    if not any_connected:
        children.append(ui.Alert(
            title="No provider connected",
            message="You can save this brief now, but generating images "
                     "will need Magnific connected first.",
            type="warning",
        ))
        children.append(ui.Button(
            "Connect Magnific", icon="Plug", variant="secondary", size="sm",
            on_click=ui.Call("__panel__studio", view="settings"),
        ))

    children.append(ui.Form(
        action="create_media_brief",
        submit_label="Create brief",
        children=[
            ui.Input(param_name="site", placeholder="Site, e.g. g4s.md"),
            ui.Input(param_name="article_title", placeholder="Article title"),
            ui.TextArea(param_name="summary", placeholder="Short summary / angle",
                        rows=4),
            ui.Input(param_name="style_direction",
                     placeholder="Style direction (optional)"),
            ui.Slider(param_name="inline_count", min=0, max=8, value=2,
                      label="Inline images besides featured"),
            ui.Select(param_name="model", options=_MODEL_OPTIONS, value="",
                      placeholder="Model (optional -- Magnific's own default if unset)"),
        ],
    ))
    children.append(ui.Button("Cancel", variant="ghost",
                              on_click=ui.Call("__panel__studio", view="")))

    return ui.Stack(children=children, gap=4)


async def _editor_existing(ctx, package_id: str, any_connected: bool) -> ui.UINode:
    row = await st.get_package(ctx, package_id)
    if row is None:
        return ui.Empty(message="This media package no longer exists.")

    assets = row.get("assets", [])
    header_badges = [_status_badge(row.get("status", "draft"))]
    if row.get("model"):
        header_badges.append(ui.Badge(label="Model", value=row["model"], color="purple"))
    header = ui.Stack(children=[
        ui.Header(text=row.get("article_title") or "(untitled brief)",
                   level=3, subtitle=row.get("site", "")),
        ui.Stack(children=header_badges, direction="h", gap=2),
    ], direction="h", justify="between")

    generate_disabled = row.get("status") == "generating" or not any_connected

    action_children = [
        ui.Button(
            "Generate all", icon="Sparkles", variant="primary",
            disabled=generate_disabled,
            on_click=ui.Call("generate_media_package", package_id=package_id),
        ),
        ui.Button("Close", variant="ghost",
                  on_click=ui.Call("__panel__studio", view="")),
    ]
    actions = ui.Stack(children=action_children, direction="h")

    children: list[ui.UINode] = [header]
    if not any_connected:
        children.append(ui.Alert(
            title="Connect Magnific to generate",
            message="This brief is saved, but generation is disabled until "
                     "a provider is connected.",
            type="warning",
        ))
    children.append(actions)
    children.append(ui.Grid(
        children=[_asset_card(package_id, a) for a in assets],
        columns=2,
    ))

    return ui.Stack(children=children, gap=4)


# ── ONE center panel, view-switched ─────────────────────────────────────────

@ext.panel(
    "studio",
    slot="center",
    title="Media Hub",
    center_overlay=True,
    refresh="manual",
)
async def studio_panel(ctx, **kwargs) -> ui.UINode:
    """The one center-overlay owner. `view` selects packages/settings/editor.

    Default view: a first-time user with no provider connected lands on
    `settings` automatically -- the same "answer what do I do now" pattern as
    Notion/Asana Connector's center panel -- instead of an empty editor.
    `connect`/`providers` are accepted as aliases for `settings` so any old
    bookmarked ui.Call still lands somewhere sensible.
    """
    view = str(kwargs.get("view") or "").strip().lower()
    package_id = str(kwargs.get("package_id") or "").strip()

    connections = await list_provider_connections(ctx)
    any_connected = any(c.connected for c in connections)

    if view in ("settings", "connect", "providers"):
        log = await md.list_log(ctx, limit=5)
        return _settings_view(ctx, connections, log)
    if view == "editor" or package_id:
        if not package_id or package_id == "new":
            return _editor_new(ctx, any_connected)
        return await _editor_existing(ctx, package_id, any_connected)

    if not any_connected:
        log = await md.list_log(ctx, limit=5)
        return _settings_view(ctx, connections, log)
    return _default_view()


def _default_view() -> ui.UINode:
    """Landing state for a connected user with no package/settings view
    picked yet -- point at the two things that actually do something
    (pick a package on the left, or start a new brief) instead of
    surfacing the settings screen unasked."""
    return ui.Stack(children=[
        ui.Empty(
            message="Pick a media package on the left, or start a new "
                    "brief to generate images.",
        ),
        ui.Button(
            "+ New brief", icon="Plus", variant="primary",
            on_click=ui.Call("__panel__studio", view="editor", package_id="new"),
        ),
    ], gap=4)

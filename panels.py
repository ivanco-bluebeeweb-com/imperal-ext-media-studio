"""Panel UI: left nav + ONE center panel, screens selected by `view`.

WHY ONE CENTER PANEL, SCREENS AS A PARAMETER (same pattern as Asana/Notion
Connector -- see their panels.py for the fuller writeup). A center slot holds
exactly ONE panel with REPLACE semantics: two panels both claiming
`slot="center"` race at session-init batch discovery, and pressing a button
that dispatches the loser looks like nothing happening. So there is exactly
one owner, `studio`, and `view` picks the screen:

    ui.Call("__panel__studio")                     -> packages (default)
    ui.Call("__panel__studio", view="connect")      -> Connect Magnific screen
    ui.Call("__panel__studio", view="providers")    -> Providers/manage screen
    ui.Call("__panel__studio", view="editor", package_id=...) -> package editor

WHY THIS REPLACES THE OLD "let the platform Secrets screen handle it" DESIGN.
v1 declared `magnific_api_key` as `write_mode="user"` and shipped with no
in-app connect screen at all, reasoning the platform's generic Secrets panel
was enough. In practice the user could not find it or tell what a "Magnific
API key" even was. The secret is now `write_mode="both"` (see app.py) and
`providers.py` validates a pasted key against Magnific before saving it, so
this file adds the two screens that make that discoverable: `connect` (first-
run) and `providers` (status + disconnect, anticipating a second provider
later without a schema change -- see `providers.list_provider_connections`).

Every component below is used strictly per its real signature in
`imperal_sdk.ui` (checked in source, not guessed) -- e.g. `ui.List` takes
`items=`, not `children=`; `ui.Form` has no `defaults=` string labels, only
`param_name`-bearing inputs; `ActionResult.success(data, summary)` is
positional, there is no `.ok()`.
"""

from __future__ import annotations

from imperal_sdk import ui

from app import ext
import storage as st
import model_registry as mr
from providers import list_provider_connections
from shared import MYSTIC_MODELS

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
                "Manage providers", icon="Plug", variant="secondary", size="sm",
                on_click=ui.Call("__panel__studio", view="providers"),
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


# ── Connect screen ───────────────────────────────────────────────────────────

def _connect_view(connections: list) -> ui.UINode:
    magnific = next((c for c in connections if c.provider == "magnific"), None)
    connected = bool(magnific and magnific.connected)

    children: list[ui.UINode] = [
        ui.Header(text="Connect Magnific", level=2,
                   subtitle="One key, checked before it's saved"),
    ]

    if connected:
        children.append(ui.Alert(
            title="Magnific is connected",
            message="Image generation is ready. Paste a new key below only "
                     "if you need to replace it (e.g. a rotated key).",
            type="success",
        ))
    else:
        children.append(ui.Alert(
            title="Not connected yet",
            message="Media briefs can be drafted without this, but "
                     "generating images needs a Magnific API key.",
            type="info",
        ))

    children.append(ui.Section(
        title="1. Get an API key",
        children=[
            ui.Text(
                content=(
                    "Magnific API keys require a Business or Enterprise "
                    "plan. Once on one of those plans: user menu -> "
                    "Organization Settings -> API Keys -> Create API key."
                ),
                variant="body",
            ),
            ui.Link(label="Open magnific.com", href=_MAGNIFIC_SIGNUP_URL),
        ],
    ))

    children.append(ui.Section(
        title="2. Paste it here",
        children=[
            ui.Text(
                content=(
                    "The key is verified against Magnific before it is "
                    "saved -- an invalid key is rejected immediately instead "
                    "of failing silently the first time you generate."
                ),
                variant="caption",
            ),
            ui.Form(
                action="connect_magnific",
                submit_label="Verify and connect",
                children=[
                    ui.Password(param_name="api_key",
                                placeholder="Magnific API key"),
                ],
            ),
        ],
    ))

    if connected:
        children.append(ui.Button(
            "Disconnect Magnific", icon="Unlink", variant="danger", size="sm",
            on_click=ui.Call("disconnect_magnific"),
        ))

    children.append(ui.Button(
        "Back", variant="ghost",
        on_click=ui.Call("__panel__studio", view="providers"),
    ))

    return ui.Stack(children=children, gap=4)


# ── Providers screen (manage / switch, anticipates more providers) ─────────

def _providers_view(connections: list) -> ui.UINode:
    children: list[ui.UINode] = [
        ui.Header(text="Providers", level=2,
                   subtitle="Image-generation backends Media Hub can use"),
    ]

    items = []
    for conn in connections:
        actions_row = ui.Stack(children=[
            ui.Button(
                "Disconnect" if conn.connected else "Connect",
                variant="secondary" if conn.connected else "primary",
                size="sm",
                on_click=(
                    ui.Call("disconnect_magnific") if conn.connected
                    else ui.Call("__panel__studio", view="connect")
                ),
            ),
        ], direction="h")

        items.append(ui.Card(
            title=conn.title,
            subtitle=conn.detail,
            content=actions_row,
        ))

    children.append(ui.Stack(children=items, gap=3))

    children.append(ui.Alert(
        title="One provider today",
        message="Magnific (Mystic) is the only image backend right now. "
                 "This screen is built to list more providers side by side "
                 "as they're added -- no redesign needed later.",
        type="info",
    ))

    children.append(ui.Button(
        "Close", variant="ghost",
        on_click=ui.Call("__panel__studio", view=""),
    ))

    return ui.Stack(children=children, gap=4)


# ── Package editor screen ────────────────────────────────────────────────────

def _asset_card(package_id: str, asset: dict) -> ui.UINode:
    role = asset.get("role", "")
    status = asset.get("status", "pending")
    body_children: list[ui.UINode] = []

    if asset.get("image_url"):
        body_children.append(ui.Image(
            src=asset["image_url"], alt=asset.get("alt_text", ""),
            width="100%", object_fit="cover",
        ))
    elif status == "generating":
        body_children.append(ui.Loading(message="Generating..."))
    elif status == "failed":
        body_children.append(ui.Alert(
            message=asset.get("error", "Generation failed."), type="error",
        ))
    else:
        body_children.append(ui.Text("Not generated yet.", variant="caption"))

    body_children.append(ui.Text(asset.get("prompt", ""), variant="caption"))
    if asset.get("model"):
        body_children.append(ui.Badge(label="Model", value=asset["model"], color="purple"))

    body_children.append(ui.Form(
        action="update_asset_meta",
        submit_label="Save alt/caption",
        defaults={"package_id": package_id, "role": role},
        children=[
            ui.Input(param_name="alt_text", placeholder="Alt text",
                     value=asset.get("alt_text", "")),
            ui.Input(param_name="caption", placeholder="Caption (optional)",
                     value=asset.get("caption", "")),
        ],
    ))

    body_children.append(ui.Form(
        action="regenerate_asset",
        submit_label="Regenerate",
        defaults={"package_id": package_id, "role": role},
        children=[
            ui.Select(param_name="model", options=_MODEL_OPTIONS,
                      value=asset.get("model", ""),
                      placeholder="Model override (optional)"),
        ],
    ))

    return ui.Card(
        title=role,
        subtitle=status,
        content=ui.Stack(children=body_children, gap=2),
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
            on_click=ui.Call("__panel__studio", view="connect"),
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
    """The one center-overlay owner. `view` selects packages/connect/providers/editor.

    Default view: a first-time user with no provider connected lands on
    `connect` automatically -- the same "answer what do I do now" pattern as
    Notion/Asana Connector's center panel -- instead of an empty editor.
    """
    view = str(kwargs.get("view") or "").strip().lower()
    package_id = str(kwargs.get("package_id") or "").strip()

    connections = await list_provider_connections(ctx)
    any_connected = any(c.connected for c in connections)

    if view == "connect":
        return _connect_view(connections)
    if view == "providers":
        return _providers_view(connections)
    if view == "editor" or package_id:
        if not package_id or package_id == "new":
            return _editor_new(ctx, any_connected)
        return await _editor_existing(ctx, package_id, any_connected)

    if not any_connected:
        return _connect_view(connections)
    return _providers_view(connections)

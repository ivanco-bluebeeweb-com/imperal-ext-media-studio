"""Panel UI: left sidebar (package list) + center-overlay editor.

WHY NO "Provider"/"Settings" PANEL HERE.

The platform already renders a generic secrets panel for every declared
`@ext.secret` (confirmed in docs.imperal.io -- EXT-SECRETS-V1). Building a
custom "paste your Magnific key" form here would duplicate that, and if it
also claimed the `right` slot it would fight the platform's own secrets UI
for the same territory. So v1 has exactly two panels: the list and the
editor; the user manages the API key through the platform's existing
secrets surface.

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
    "packages",
    slot="left",
    title="Media Packages",
    default_width=300,
    min_width=220,
    max_width=420,
    refresh="on_event:media-studio.create_media_brief,media-studio.generate_media_package,"
            "media-studio.delete_media_package",
)
async def packages_panel(ctx) -> ui.UINode:
    rows = await st.list_packages(ctx, limit=100)

    header = ui.Stack(children=[
        ui.Button("+ New brief", icon="Plus", variant="primary",
                  on_click=ui.Call("__panel__editor", package_id="new")),
    ], direction="h", justify="end")

    if not rows:
        return ui.Stack(children=[
            header,
            ui.Empty(message="No media packages yet -- create a brief to "
                              "generate a featured image plus inline images."),
        ], gap=4)

    items = [
        ui.ListItem(
            id=r["id"],
            title=r.get("article_title") or "(untitled brief)",
            subtitle=r.get("site", ""),
            meta=_asset_progress(r.get("assets", [])),
            badge=_status_badge(r.get("status", "draft")),
            on_click=ui.Call("__panel__editor", package_id=r["id"]),
            actions=[{
                "icon": "Trash2",
                "on_click": ui.Call("delete_media_package", package_id=r["id"]),
                "confirm": f"Delete media package '{r.get('article_title') or r['id']}'?",
            }],
        )
        for r in rows
    ]

    return ui.Stack(children=[header, ui.List(items=items, searchable=True)], gap=3)


# ── Center-overlay editor ────────────────────────────────────────────────────

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

    body_children.append(ui.Stack(children=[
        ui.Button(
            "Regenerate", icon="RefreshCw", variant="secondary", size="sm",
            on_click=ui.Call("regenerate_asset", package_id=package_id, role=role),
        ),
    ], direction="h"))

    return ui.Card(
        title=role,
        subtitle=status,
        content=ui.Stack(children=body_children, gap=2),
    )


def _editor_new(ctx) -> ui.UINode:
    return ui.Stack(children=[
        ui.Header(text="New media brief", level=3),
        ui.Form(
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
            ],
        ),
        ui.Button("Cancel", variant="ghost",
                  on_click=ui.Call("__panel__editor", package_id="")),
    ], gap=4)


async def _editor_existing(ctx, package_id: str) -> ui.UINode:
    row = await st.get_package(ctx, package_id)
    if row is None:
        return ui.Empty(message="This media package no longer exists.")

    assets = row.get("assets", [])
    header = ui.Stack(children=[
        ui.Header(text=row.get("article_title") or "(untitled brief)",
                   level=3, subtitle=row.get("site", "")),
        _status_badge(row.get("status", "draft")),
    ], direction="h", justify="between")

    actions = ui.Stack(children=[
        ui.Button(
            "Generate all", icon="Sparkles", variant="primary",
            disabled=row.get("status") == "generating",
            on_click=ui.Call("generate_media_package", package_id=package_id),
        ),
        ui.Button("Close", variant="ghost",
                  on_click=ui.Call("__panel__editor", package_id="")),
    ], direction="h")

    grid = ui.Grid(
        children=[_asset_card(package_id, a) for a in assets],
        columns=2,
    )

    return ui.Stack(children=[header, actions, grid], gap=4)


@ext.panel(
    "editor",
    slot="center",
    title="Media Package Editor",
    center_overlay=True,
    refresh="on_event:media-studio.generate_media_package,media-studio.regenerate_asset,"
            "media-studio.update_asset_meta",
)
async def editor_panel(ctx, package_id: str = "") -> ui.UINode:
    if not package_id or package_id == "new":
        return _editor_new(ctx)
    return await _editor_existing(ctx, package_id)

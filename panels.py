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
import prompt_engine as pe
import magnific_client as mc
import storage as st
import model_registry as mr
from providers import list_provider_connections
from shared import MYSTIC_MODELS

# Google Imagen 4 Ultra/Fast are excluded here on purpose -- standing user
# directive (see model_registry.DISABLED_MODEL_IDS). Their ModelSpec rows
# stay in mr.MODELS only for recovery.py's historical lookups; they must
# never appear as a choosable option in this dropdown.
_MODEL_OPTIONS = (
    [{"value": "", "label": "Mystic default"},
     {"value": "auto", "label": "Auto (Media Hub picks the best model)"}]
    + [{"value": m, "label": m.replace("_", " ")} for m in MYSTIC_MODELS]
    + [{"value": m_id, "label": spec.label} for m_id, spec in mr.MODELS.items()
       if m_id != "mystic" and m_id not in mr.DISABLED_MODEL_IDS]
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

async def _projects_section(ctx, site: str, show_add_project: str) -> ui.UINode:
    """'Projects' = the sites we already work on -- every distinct `site`
    value already used by a media package, PLUS any project registered
    explicitly via the 'Add new project' dialog even before its first
    brief exists. Shown as a clickable list so a project is always one
    click away; clicking one routes the center panel to that project's
    brief catalogue (studio_panel(site=...)), styled exactly like the
    existing packages catalogue -- just scoped and re-titled.
    """
    projects = await st.list_projects(ctx)

    project_items = [
        ui.ListItem(
            id=p["site_id"],
            title=p["name"],
            subtitle=p["site_id"] if p["name"] != p["site_id"] else "",
            meta=f"{p['brief_count']} briefs",
            selected=(p["site_id"] == site and bool(site)),
            on_click=ui.Call("__panel__studio", site=p["site_id"]),
        )
        for p in projects
    ]

    add_project_button = ui.Button(
        "➕ Add new project", variant="secondary", size="sm", full_width=True,
        on_click=ui.Call("__panel__packages_nav", site=site, show_add_project="1"),
    )

    children: list[ui.UINode] = [add_project_button]
    if project_items:
        children.append(ui.List(items=project_items))
    else:
        children.append(ui.Empty(message="No projects yet — add one to get started.", icon="🗂️"))

    if show_add_project:
        children.append(
            ui.Dialog(
                title="Add new project",
                content=ui.Stack(
                    direction="v", gap=2,
                    children=[
                        ui.Input(param_name="site_id", placeholder="Site id, e.g. g4s.md"),
                        ui.Input(param_name="name", placeholder="Display name (optional)"),
                    ],
                ),
                confirm_label="Add project",
                cancel_label="Cancel",
                on_confirm=ui.Call("create_project"),
            )
        )

    return ui.Stack(
        direction="v", gap=2,
        children=[ui.Header(text="Projects", level=3), *children],
    )


@ext.panel(
    "packages_nav",
    slot="left",
    title="Media Hub",
    default_width=300,
    min_width=220,
    max_width=420,
    refresh="on_event:media-studio.create_media_brief,media-studio.generate_media_package,"
            "media-studio.delete_media_package,media-studio.connect_magnific,"
            "media-studio.disconnect_magnific,media-studio.create_project",
)
async def packages_nav_panel(ctx, site: str = "", show_add_project: str = "", **kwargs) -> ui.UINode:
    """Projects list PLUS a single 'App settings' entry point.

    UI_INTERFACE_STANDARD.md / sidebar-cards rule: no visually-boxed
    container (no title/subtitle Card) for this block -- just the button,
    after a Divider. Provider connection status (Magnific, and later other
    providers) lives INSIDE the settings screen itself, not duplicated here
    as a preview line -- one place to look, not two.
    """
    projects_section = await _projects_section(ctx, site, show_add_project)

    settings_button = ui.Button(
        "App settings", icon="Settings", variant="secondary", size="sm",
        full_width=True,
        on_click=ui.Call("__panel__studio", view="settings"),
    )

    return ui.Stack(children=[projects_section, ui.Divider(), settings_button], gap=3)


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

def _settings_view(
    ctx, connections: list, log: list[dict], prompt_log: list[dict],
    prompt_config: dict | None = None,
) -> ui.UINode:
    """App settings screen. UI_INTERFACE_STANDARD.md rule 1-3 (one button,
    one screen, everything configurable) PLUS the tabs-first navigation
    rule: ui.Tabs is the primary navigator across sub-sections, not a flat
    stack of ui.Section blocks one under another. Each former Section
    becomes one tab; tab content is the same children, just wrapped.

    WHY THE FIRST TAB IS "Providers" (plural), NOT "Image provider". This
    screen manages exactly one provider today (Magnific), but the model
    registry already supports routing to several providers' models
    (Gemini/Nano Banana already reachable through Magnific; a future direct
    Gemini/other key is the next seam -- see providers.py's own note on
    _KNOWN_PROVIDERS). Building the tab as a per-provider LIST from the
    start means adding a second provider later is one more row in this
    list, not a second tab or a rewrite of this function.
    """
    magnific = next((c for c in connections if c.provider == "magnific"), None)
    connected = bool(magnific and magnific.connected)

    # -- Providers tab -- one row per known provider ---------------------
    providers_tab = ui.Stack(direction="v", gap=3, children=[
        ui.Header(text="Image provider", level=3),
        *(_provider_form_children(connected)),
    ])

    # -- Webhook secret tab (declared, not used yet -- say so honestly) --
    webhook_tab = ui.Stack(direction="v", gap=2, children=[
        ui.Text(
            content="Not needed yet -- Media Hub checks image status "
                    "itself, without webhooks.",
            variant="caption",
        ),
    ])

    # -- New model checks tab (was chat-only; now visible here too) ------
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
    discovery_tab = ui.Stack(direction="v", gap=2, children=discovery_children)

    # -- Image Prompt engine tab (monthly self-review, visible here too) -
    prompt_config = prompt_config or {}
    prompt_children: list[ui.UINode] = [
        ui.Text(
            content="Media Hub checks once a month whether its own image "
                    "prompts still follow current best practice, and "
                    "re-scores a sample of its own recent prompts. It only "
                    "reports what it finds -- it never rewrites its own "
                    "rules by itself.",
            variant="caption",
        ),
        ui.Button(
            "Check now", icon="RefreshCw", variant="secondary",
            size="sm", on_click=ui.Call("check_prompt_engine_updates"),
        ),
        ui.Divider(),
        ui.Header(text="Settings", level=3,
                   subtitle="The generic clauses this engine falls back to, "
                            "and the review alert threshold. Leave a field "
                            "blank to keep its current value."),
        ui.Form(
            action="save_prompt_engine_config",
            submit_label="Apply Changes",
            children=[
                ui.Toggle(
                    label="Forbid text in generated images",
                    param_name="forbid_image_text",
                    value=bool(prompt_config.get("forbid_image_text", True)),
                ),
                ui.Text(
                    "On by default: every generated image is required to be "
                    "clean, with no legible labels, signage, or captions "
                    "baked in. Turn this off only for a brief where "
                    "rendering exact in-image text is genuinely the right "
                    "call -- the brief must still supply the exact wording "
                    "itself (image_text); this switch only lifts the "
                    "blanket ban, it never invents text on its own.",
                    variant="caption",
                ),
                ui.Divider(),
                ui.Text("Generic lighting clause", variant="label"),
                ui.TextArea(
                    param_name="generic_lighting",
                    placeholder="Appended when a prompt has no lighting language.",
                    value=prompt_config.get("generic_lighting", ""),
                ),
                ui.Text("Generic camera/lens clause -- featured", variant="label"),
                ui.TextArea(
                    param_name="generic_camera_featured",
                    placeholder="Appended to FEATURED prompts missing camera/lens language.",
                    value=prompt_config.get("generic_camera_featured", ""),
                ),
                ui.Text("Generic camera/lens clause -- inline", variant="label"),
                ui.TextArea(
                    param_name="generic_camera_inline",
                    placeholder="Appended to INLINE prompts missing camera/lens language.",
                    value=prompt_config.get("generic_camera_inline", ""),
                ),
                ui.Text("Generic style fallback", variant="label"),
                ui.Input(
                    param_name="generic_style",
                    placeholder="Appended when a prompt names no style/medium at all.",
                    value=prompt_config.get("generic_style", ""),
                ),
                ui.Text("Review alert threshold (0-100)", variant="label"),
                ui.Input(
                    param_name="score_alert_threshold",
                    placeholder=str(prompt_config.get("score_alert_threshold", "")),
                    value=str(prompt_config.get("score_alert_threshold", "")),
                ),
            ],
        ),
    ]
    if prompt_log:
        items = [
            ui.ListItem(
                id=entry.get("checked_at", ""),
                title=entry.get("checked_at", ""),
                subtitle=(
                    f"avg score {entry.get('avg_score', 0)}/100"
                    + (" -- review recommended" if entry.get("review_recommended") else "")
                ),
                meta=entry.get("note", ""),
            )
            for entry in prompt_log
        ]
        prompt_children.append(ui.List(items=items))
    else:
        prompt_children.append(ui.Empty(
            message="No reviews yet -- click Check now above.",
        ))
    prompt_tab = ui.Stack(direction="v", gap=2, children=prompt_children)

    # -- Image storage tab ------------------------------------------------
    storage_tab = ui.Stack(direction="v", gap=2, children=[
        ui.Text(
            "Generated images are copied into Media Hub storage and stay available until you delete them. "
            "Use this once to restore older images that were saved with an expired provider link.",
            variant="caption",
        ),
        ui.Button(
            "Restore older images", variant="secondary",
            on_click=ui.Call("recover_stored_images"),
        ),
    ])

    tabs = ui.Tabs(tabs=[
        {"label": "Providers", "content": providers_tab},
        {"label": "New model checks", "content": discovery_tab},
        {"label": "Image Prompt engine", "content": prompt_tab},
        {"label": "Image storage", "content": storage_tab},
        {"label": "Webhook secret", "content": webhook_tab},
    ])

    return ui.Stack(children=[
        ui.Header(text="App settings", level=2,
                   subtitle="Everything you can configure in Media Hub"),
        tabs,
        ui.Button(
            "Close", variant="ghost",
            on_click=ui.Call("__panel__studio", view="", package_id=""),
        ),
    ], gap=4)


def _provider_form_children(connected: bool) -> list[ui.UINode]:
    """The Magnific connect/disconnect form -- pulled out so the Providers
    tab reads as one row per provider even while there is only one."""
    children: list[ui.UINode] = []
    if connected:
        children.append(ui.Alert(
            title="Magnific connected", message="Images are ready to generate.",
            type="success",
        ))
    else:
        children.append(ui.Alert(
            title="Not connected",
            message="Paste an API key below to start generating images.",
            type="info",
        ))
        children.append(ui.Link(
            label="Get a key on magnific.com", href=_MAGNIFIC_SIGNUP_URL,
        ))
    children.append(ui.Form(
        action="connect_magnific",
        submit_label="Verify and connect",
        children=[
            ui.Password(param_name="api_key", placeholder="Magnific API key"),
        ],
    ))
    if connected:
        children.append(ui.Button(
            "Disconnect", icon="Unlink", variant="danger", size="sm",
            on_click=ui.Call("disconnect_magnific"),
        ))
    return children


# ── Package editor screen ────────────────────────────────────────────────────

def _asset_card(package_id: str, asset: dict) -> ui.UINode:
    role = asset.get("role", "")
    status = asset.get("status", "pending")
    image_children: list[ui.UINode] = []

    # A ready image remains visible for the lifetime of its stored asset.
    # Fresh assets use Imperal Storage; legacy assets are shown while the
    # recovery pass replaces their provider URL with the stored copy.
    original_url = asset.get("original_image_url") or asset.get("image_url", "")
    if original_url:
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
            ui.Button(
                "Delete", variant="ghost", size="sm",
                on_click=ui.Call("delete_asset_image", package_id=package_id, role=role, version="original"),
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
                ui.Button(
                    "Delete", variant="ghost", size="sm",
                    on_click=ui.Call("delete_asset_image", package_id=package_id, role=role, version="upscaled"),
                ),
            ])
    elif status == "generating":
        image_children.append(ui.Loading(message="Generating..."))
    elif status == "failed":
        image_children.append(ui.Alert(message=asset.get("error", "Generation failed."), type="error"))
    else:
        image_children.append(ui.Text("Not generated yet.", variant="caption"))

    image_title = asset.get("filename") or _asset_title(role)
    upscale_children: list[ui.UINode] = []
    if original_url:
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
            ui.Text("Image Prompt", variant="label"),
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
            ui.Header(f"{role_title} Image", level=2),
            _status_badge(status),
        ],
        gap=2,
    ))
    return ui.Card(
        content=ui.Stack(children=image_children, gap=2),
    )


async def _editor_new(ctx, any_connected: bool, site: str = "") -> ui.UINode:
    """New-brief form. When `site` names a project with saved defaults
    (Project Overview tab -> default_style_direction / default_lang), those
    pre-fill this form's own fields -- a real default, not just stored text
    -- while staying fully editable/overridable per brief."""
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

    default_style = ""
    default_lang = ""
    if site:
        projects = await st.list_projects(ctx)
        project = next((p for p in projects if p["site_id"] == site), None)
        if project:
            default_style = project.get("default_style_direction", "")
            default_lang = project.get("default_lang", "")

    children.append(ui.Form(
        action="create_media_brief",
        submit_label="Create brief",
        children=[
            ui.Input(param_name="site", placeholder="Site, e.g. g4s.md", value=site),
            ui.Input(param_name="article_title", placeholder="Article title"),
            ui.TextArea(param_name="summary", placeholder="Short summary / angle",
                        rows=4),
            ui.Input(param_name="style_direction",
                     placeholder="Style direction (optional)", value=default_style),
            ui.Input(param_name="lang",
                     placeholder="Post language, e.g. ru, ro (optional)", value=default_lang),
            ui.Slider(param_name="inline_count", min=0, max=8, value=2,
                      label="Inline images besides featured"),
            ui.Select(param_name="model", options=_MODEL_OPTIONS, value="",
                      placeholder="Model (optional -- Magnific's own default if unset)"),
        ],
    ))
    children.append(ui.Button("Cancel", variant="ghost",
                              on_click=ui.Call("__panel__studio", view="", package_id="", site=site)))

    return ui.Stack(children=children, gap=4)

def _brief_overview_tab(package_id: str, row: dict) -> ui.UINode:
    """Brief Overview tab content: \"О брифе\" -- the media strategy for this
    one content unit (why these images, what they must communicate, how
    they support the article). Editable in place via update_brief_overview.
    """
    return ui.Stack(children=[
        ui.Text(
            "Why these specific images exist, what each must visually "
            "communicate, and how they support this article's angle and "
            "the reader's journey through it.",
            variant="caption",
        ),
        ui.Form(
            action="update_brief_overview",
            submit_label="Save Changes",
            defaults={"package_id": package_id},
            children=[
                ui.Text("О брифе", variant="label"),
                ui.TextArea(
                    param_name="media_strategy",
                    placeholder="The media strategy for this content unit...",
                    value=row.get("media_strategy", ""), rows=6,
                ),
            ],
        ),
    ], gap=3)


def _brief_assets_tab(package_id: str, row: dict, any_connected: bool) -> ui.UINode:
    """Second tab: the previous top-level content of a brief's detail page
    (unchanged) -- Generate all / Close actions plus the asset grid."""
    assets = row.get("assets", [])
    generate_disabled = row.get("status") == "generating" or not any_connected

    action_children = [
        ui.Button(
            "Generate all", icon="Sparkles", variant="primary",
            disabled=generate_disabled,
            on_click=ui.Call("generate_media_package", package_id=package_id),
        ),
        ui.Button("Close", variant="ghost",
                  on_click=ui.Call("__panel__studio", view="", package_id="")),
    ]
    actions = ui.Stack(children=action_children, direction="h")

    children: list[ui.UINode] = []
    if not any_connected:
        children.append(ui.Alert(
            title="Connect Magnific to generate",
            message="This brief is saved, but generation is disabled until "
                     "a provider is connected.",
            type="warning",
        ))
    children.append(actions)
    children.append(ui.Grid(children=[
        _asset_card(package_id, a) for a in assets
    ], columns=2, gap=4))

    return ui.Stack(children=children, gap=4)


async def _editor_existing(ctx, package_id: str, any_connected: bool) -> ui.UINode:
    row = await st.get_package(ctx, package_id)
    if row is None:
        return ui.Empty(message="This media package no longer exists.")

    header_badges = [_status_badge(row.get("status", "draft"))]
    if row.get("model"):
        header_badges.append(ui.Badge(label="Model", value=row["model"], color="purple"))
    header = ui.Stack(children=[
        ui.Header(text=row.get("article_title") or "(untitled brief)",
                   level=3, subtitle=row.get("site", "")),
        ui.Stack(children=header_badges, direction="h", gap=2),
    ], direction="h", justify="between")

    children: list[ui.UINode] = [
        ui.Stack(children=[
            ui.Button(
                "All media briefs", icon="ArrowLeft", variant="ghost", size="sm",
                on_click=ui.Call("__panel__studio", view="", package_id=""),
            ),
        ], direction="h", justify="start"),
        header,
        ui.Tabs(tabs=[
            {"label": "Brief Overview", "content": _brief_overview_tab(package_id, row)},
            {"label": "Assets", "content": _brief_assets_tab(package_id, row, any_connected)},
        ]),
    ]

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

    `site` scopes the packages view to one project's briefs (set by clicking
    a project in the sidebar); empty means "every brief", same as before.
    """
    view = str(kwargs.get("view") or "").strip().lower()
    package_id = str(kwargs.get("package_id") or "").strip()
    site = str(kwargs.get("site") or "").strip()

    connections = await list_provider_connections(ctx)
    any_connected = any(c.connected for c in connections)

    if view in ("settings", "connect", "providers"):
        log = await md.list_log(ctx, limit=5)
        prompt_log = await pe.list_review_log(ctx, limit=5)
        prompt_config = await pe.get_prompt_config(ctx)
        return _settings_view(ctx, connections, log, prompt_log, prompt_config)
    if view == "editor" or package_id:
        if not package_id or package_id == "new":
            return await _editor_new(ctx, any_connected, site=site)
        return await _editor_existing(ctx, package_id, any_connected)

    return await _packages_view(ctx, any_connected, site=site)


_STATUS_ORDER = ["ready", "generating", "partial", "draft", "failed"]


def _status_breakdown(rows: list[dict]) -> str:
    """'15 ready, 1 draft, 2 failed' -- a live summary of every state a
    brief can be in, not a guess at what "search" might mean."""
    if not rows:
        return "No briefs yet."
    counts: dict[str, int] = {}
    for row in rows:
        status = row.get("status") or "draft"
        counts[status] = counts.get(status, 0) + 1
    parts = [f"{counts.pop(s)} {s}" for s in _STATUS_ORDER if s in counts]
    parts += [f"{n} {s}" for s, n in counts.items()]
    return ", ".join(parts)


def _brief_meta(row: dict) -> str:
    """Everything worth typing into search, rendered as the item's visible
    meta line: asset progress (e.g. '2/3 ready'), the model, and the status
    word -- so typing a model name or a ratio like '1/1' finds a brief too,
    not just its title or site."""
    bits = [_asset_progress(row.get("assets", []))]
    model = row.get("model") or ""
    if model:
        bits.append(model)
    bits.append(row.get("status") or "draft")
    return " \u00b7 ".join(bits)


async def _project_overview_tab(ctx, site: str) -> ui.UINode:
    """Project Overview tab content: \"О проекте\" plus the two per-project
    defaults (style_direction, lang) that pre-fill every NEW brief created
    for this project. Editable in place via one Form -> update_project.

    Works even for an \"implicit\" project (one that only exists because a
    brief already references this site, never explicitly created) -- the
    form still submits fine; update_project auto-creates the row.
    """
    projects = await st.list_projects(ctx)
    project = next((p for p in projects if p["site_id"] == site), None)
    about = project.get("about", "") if project else ""
    default_style = project.get("default_style_direction", "") if project else ""
    default_lang = project.get("default_lang", "") if project else ""

    return ui.Stack(children=[
        ui.Text(
            "Context for whoever works this project -- manually or through "
            "this app alone. Defaults set here pre-fill every NEW brief; "
            "an individual brief can still override them.",
            variant="caption",
        ),
        ui.Form(
            action="update_project",
            submit_label="Save Changes",
            defaults={"site_id": site},
            children=[
                ui.Text("О проекте", variant="label"),
                ui.TextArea(
                    param_name="about",
                    placeholder="What this site/brand is, its audience, and any notes.",
                    value=about, rows=4,
                ),
                ui.Text("Style direction по умолчанию", variant="label"),
                ui.Input(
                    param_name="default_style_direction",
                    placeholder="e.g. industrial, realistic, no text, blue/grey palette",
                    value=default_style,
                ),
                ui.Text("Язык по умолчанию (lang)", variant="label"),
                ui.Input(
                    param_name="default_lang",
                    placeholder="e.g. ru, ro",
                    value=default_lang,
                ),
            ],
        ),
    ], gap=3)


def _packages_body(rows: list[dict], any_connected: bool, site: str, header_text: str) -> ui.UINode:
    """The actual brief catalogue content -- header, New brief button,
    optional provider warning, and the searchable list/empty state.
    Shared by the unscoped view and the project-scoped 'Briefs' tab."""
    new_brief_action = ui.Call("__panel__studio", view="editor", package_id="new", site=site)

    children: list[ui.UINode] = [
        ui.Header(text=header_text, level=2,
                   subtitle=_status_breakdown(rows)),
        ui.Stack(children=[
            ui.Button(
                "New brief", icon="Plus", variant="primary",
                on_click=new_brief_action,
            ),
        ], direction="h", justify="end"),
    ]

    if not any_connected:
        children.append(ui.Alert(
            title="Connect a provider to generate images",
            message="You can still create briefs. Connect Magnific in App settings before generating images.",
            type="warning",
        ))

    if not rows:
        children.append(ui.Empty(
            message="No media briefs yet. Create one to prepare a featured image and inline images.",
        ))
    else:
        items = [
            ui.ListItem(
                id=row["id"],
                title=row.get("article_title") or "(untitled brief)",
                subtitle=row.get("site", "") or "No site specified",
                meta=_brief_meta(row),
                badge=_status_badge(row.get("status", "draft")),
                on_click=ui.Call("__panel__studio", view="editor", package_id=row["id"]),
                actions=[{
                    "icon": "Trash2",
                    "on_click": ui.Call("delete_media_package", package_id=row["id"]),
                    "confirm": f"Delete media package '{row.get('article_title') or row['id']}'?",
                }],
            )
            for row in rows
        ]
        children.append(ui.List(items=items, searchable=True))

    return ui.Stack(children=children, gap=4)


async def _packages_view(ctx, any_connected: bool, site: str = "") -> ui.UINode:
    """Central catalogue of media briefs -- every brief, or (when `site` is
    given) just one project's briefs.

    Unscoped (no site): the brief catalogue only, unchanged.
    Project-scoped (site set): wrapped in tabs -- 'Project Overview' first
    ("О проекте" + default style_direction/lang for this project), then
    'Briefs' with the exact same catalogue content as before.

    WHY `ui.List(searchable=True)`, NOT OUR OWN SEARCH INPUT. The user wants
    real as-you-type filtering over the FULL list already loaded from the
    backend -- no submit button, no server round-trip per keystroke. This SDK
    has exactly one component that behaves that way: `ui.List(searchable=True)`
    filters client-side over items already on the page. A standalone
    `ui.Input` here only fires `on_submit` (Enter), which is a step backwards.
    The tradeoff (documented, not hidden): the List's own search box renders
    INSIDE the List, so it cannot share a row with an external button --
    that slot doesn't exist in this SDK. The "New brief" button instead sits
    directly above the list, as close to it as layout allows.
    """
    rows = await st.list_packages(ctx, site=site, limit=100)
    total = len(rows)

    if site:
        projects = await st.list_projects(ctx)
        project = next((p for p in projects if p["site_id"] == site), None)
        project_label = project["name"] if project else site
        header_text = f"{project_label} · Briefs ({total})"
        briefs_body = _packages_body(rows, any_connected, site, header_text)
        overview = await _project_overview_tab(ctx, site)
        return ui.Tabs(tabs=[
            {"label": "Project Overview", "content": overview},
            {"label": "Briefs", "content": briefs_body},
        ])

    header_text = f"Media briefs ({total})"
    return _packages_body(rows, any_connected, site, header_text)

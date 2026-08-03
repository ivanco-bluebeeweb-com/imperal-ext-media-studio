"""Chat-function tools.

WHY GENERATION RUNS IN THE BACKGROUND.

A package with N images means N sequential Mystic jobs (create + poll each
one), and Mystic generation + polling can easily exceed the 30s default
ctx.http timeout even for a single image, let alone several. Following the
confirmed-working pattern in SEO Audit Engine's `audit_sites`: the tool
returns an immediate acknowledgement, then `ctx.background_task(work())`
runs the real work and the platform auto-delivers `work()`'s returned
ActionResult as a fresh chat message when it finishes.

`background=True` on the decorator is ONLY advisory metadata (confirmed by
reading imperal_sdk 5.9.12/13 source -- there is no automatic wrapping); the
actual detachment is the explicit `ctx.background_task(...)` call below.
"""

from __future__ import annotations

from imperal_sdk import ActionResult, sdl

import codes as c
import magnific_client as mc
import storage as st
from app import chat, ext
from models import (
    CreateMediaBriefParams,
    DeleteMediaPackageParams,
    DeleteResult,
    GenerateMediaPackageParams,
    GetMediaPackageParams,
    ListMediaPackagesParams,
    MediaAsset,
    MediaPackage,
    RegenerateAssetParams,
    UpdateAssetMetaParams,
)
from shared import default_alt_text, error as _error, prompt_for_role, roles_for


def _asset_title(role: str) -> str:
    return "Featured image" if role == "featured" else f"Inline image ({role})"


def _package_to_entity(row: dict) -> MediaPackage:
    assets = [
        MediaAsset(
            id=a.get("id", a.get("role", "")),
            title=_asset_title(a.get("role", "")),
            role=a.get("role", ""),
            provider=a.get("provider", "magnific"),
            status=a.get("status", "pending"),
            image_url=a.get("image_url", ""),
            alt_text=a.get("alt_text", ""),
            caption=a.get("caption", ""),
            prompt=a.get("prompt", ""),
            error_message=a.get("error", ""),
        )
        for a in row.get("assets", [])
    ]
    return MediaPackage(
        id=row.get("id", ""),
        title=row.get("article_title") or "(untitled brief)",
        site=row.get("site", ""),
        article_title=row.get("article_title", ""),
        summary=row.get("summary", ""),
        style_direction=row.get("style_direction", ""),
        status=row.get("status", "draft"),
        assets=assets,
        created_at=row.get("created_at", ""),
        updated_at=row.get("updated_at", ""),
    )


@chat.function(
    "create_media_brief",
    "Create a new image brief for an article: site, title, summary and how "
    "many inline images besides the featured image. Creates the package in "
    "draft status -- call generate_media_package next to actually produce "
    "the images.",
    action_type="write",
    data_model=MediaPackage,
    event="media-studio.create_media_brief",
    effects=["create:media_package"],
)
async def create_media_brief(ctx, params: CreateMediaBriefParams) -> ActionResult:
    """Create a draft media package with one pending asset per role."""
    if not params.summary.strip() and not params.article_title.strip():
        return _error(
            "Give at least an article title or a summary to base the "
            "images on.", c.MEDIA_EMPTY_BRIEF,
        )

    roles = roles_for(params.inline_count)
    assets = [
        {
            "id": role,
            "role": role,
            "provider": "magnific",
            "status": "pending",
            "image_url": "",
            "alt_text": "",
            "caption": "",
            "prompt": prompt_for_role(
                role, params.article_title, params.summary, params.style_direction,
            ),
            "error": "",
        }
        for role in roles
    ]

    package_id, row = await st.create_package(ctx, {
        "site": params.site,
        "article_title": params.article_title,
        "summary": params.summary,
        "style_direction": params.style_direction,
        "status": "draft",
        "assets": assets,
    })
    row["id"] = package_id
    return ActionResult.success(
        _package_to_entity(row),
        f"Created a media brief with {len(roles)} image slot(s) "
        f"({', '.join(roles)}). Call generate_media_package to produce them.",
    )


@chat.function(
    "generate_media_package",
    "Generate all pending images for a media package via Magnific (Mystic). "
    "Runs in the background -- you get an immediate acknowledgement, then a "
    "follow-up message when the images are ready (or on failure).",
    action_type="write",
    background=True,
    long_running=True,
    data_model=MediaPackage,
    event="media-studio.generate_media_package",
    effects=["update:media_package"],
)
async def generate_media_package(ctx, params: GenerateMediaPackageParams) -> ActionResult:
    """Generate every pending asset in a package via Magnific, in the background."""
    row = await st.get_package(ctx, params.package_id)
    if row is None:
        return _error(
            f"No media package found with id '{params.package_id}'.",
            c.MEDIA_PACKAGE_NOT_FOUND,
        )
    if row.get("status") == "generating":
        return _error(
            "This package is already generating -- wait for it to finish "
            "before starting again.", c.MEDIA_ALREADY_GENERATING,
        )

    api_key = await ctx.secrets.get("magnific_api_key")
    if not api_key:
        return _error(
            "No Magnific API key connected yet. Open Media Studio settings "
            "and paste your Magnific API key first.",
            c.MEDIA_KEY_NOT_CONFIGURED,
        )

    await st.update_package(ctx, params.package_id, {"status": "generating"})

    async def work() -> ActionResult:
        current = await st.get_package(ctx, params.package_id)
        assets = list(current.get("assets", []))
        total = len(assets) or 1
        any_failed = False
        for i, asset in enumerate(assets):
            if asset.get("status") == "ready":
                continue
            try:
                await ctx.progress(
                    (i / total) * 100,
                    f"Generating {asset.get('role', 'image')}...",
                )
                image_url = await mc.generate_image(ctx, api_key, asset["prompt"])
                asset["image_url"] = image_url
                asset["status"] = "ready"
                asset["error"] = ""
                if not asset.get("alt_text"):
                    asset["alt_text"] = default_alt_text(
                        asset["role"], current.get("article_title", ""),
                    )
            except mc.ProviderError as exc:
                asset["status"] = "failed"
                asset["error"] = str(exc)
                any_failed = True
            await st.update_package(ctx, params.package_id, {"assets": assets})

        final_status = "failed" if any_failed and all(
            a.get("status") == "failed" for a in assets
        ) else ("partial" if any_failed else "ready")
        final_row = await st.update_package(
            ctx, params.package_id, {"status": final_status, "assets": assets},
        )
        if final_status == "failed":
            return _error(
                "All images in this package failed to generate. Check the "
                "per-asset error and try generate_media_package again.",
                c.MEDIA_PROVIDER_ERROR,
            )
        done = sum(1 for a in assets if a.get("status") == "ready")
        return ActionResult.success(
            _package_to_entity(final_row),
            f"Media package ready: {done}/{len(assets)} image(s) generated "
            f"({final_status}).",
        )

    await ctx.background_task(work(), long_running=True, name="media-studio-generate")
    return ActionResult.success(
        _package_to_entity({**row, "status": "generating"}),
        f"Started generating {len(row.get('assets', []))} image(s). "
        "I'll message you here when they're ready.",
    )


@chat.function(
    "list_media_packages",
    "List media packages, optionally filtered by site or status.",
    action_type="read",
    data_model=MediaPackage,
    event="media-studio.list_media_packages",
)
async def list_media_packages(ctx, params: ListMediaPackagesParams) -> ActionResult:
    """List media packages, optionally filtered by site or status."""
    rows = await st.list_packages(ctx, site=params.site, status=params.status, limit=params.limit)
    packages = [_package_to_entity(r) for r in rows]
    return ActionResult.success(
        sdl.EntityList(items=packages),
        f"Found {len(packages)} media package(s).",
    )


@chat.function(
    "get_media_package",
    "Get one media package in full, including every asset's status, image "
    "URL, alt text and caption.",
    action_type="read",
    data_model=MediaPackage,
    event="media-studio.get_media_package",
)
async def get_media_package(ctx, params: GetMediaPackageParams) -> ActionResult:
    """Fetch one media package in full, including every asset."""
    row = await st.get_package(ctx, params.package_id)
    if row is None:
        return _error(
            f"No media package found with id '{params.package_id}'.",
            c.MEDIA_PACKAGE_NOT_FOUND,
        )
    entity = _package_to_entity(row)
    return ActionResult.success(entity, f"Media package '{entity.article_title or entity.id}' -- status {entity.status}.")


@chat.function(
    "regenerate_asset",
    "Re-generate a single image within a package (e.g. just the featured "
    "image) without touching the other assets. Optionally override the "
    "prompt for this one image.",
    action_type="write",
    background=True,
    long_running=True,
    data_model=MediaAsset,
    event="media-studio.regenerate_asset",
    effects=["update:media_package"],
)
async def regenerate_asset(ctx, params: RegenerateAssetParams) -> ActionResult:
    """Re-generate exactly one asset within a package, in the background."""
    row = await st.get_package(ctx, params.package_id)
    if row is None:
        return _error(
            f"No media package found with id '{params.package_id}'.",
            c.MEDIA_PACKAGE_NOT_FOUND,
        )
    assets = list(row.get("assets", []))
    target = next((a for a in assets if a.get("role") == params.role), None)
    if target is None:
        return _error(
            f"No asset '{params.role}' in package '{params.package_id}'.",
            c.MEDIA_ASSET_NOT_FOUND,
        )

    api_key = await ctx.secrets.get("magnific_api_key")
    if not api_key:
        return _error(
            "No Magnific API key connected yet. Open Media Studio settings "
            "and paste your Magnific API key first.",
            c.MEDIA_KEY_NOT_CONFIGURED,
        )

    if params.prompt_override.strip():
        target["prompt"] = params.prompt_override.strip()
    target["status"] = "generating"
    await st.update_package(ctx, params.package_id, {"assets": assets})

    async def work() -> ActionResult:
        try:
            image_url = await mc.generate_image(ctx, api_key, target["prompt"])
            target["image_url"] = image_url
            target["status"] = "ready"
            target["error"] = ""
        except mc.ProviderError as exc:
            target["status"] = "failed"
            target["error"] = str(exc)
        current = await st.update_package(ctx, params.package_id, {"assets": assets})
        current_assets = {a["role"]: a for a in current.get("assets", [])}
        refreshed = current_assets.get(params.role, target)
        if refreshed.get("status") == "failed":
            return _error(
                f"Regenerating '{params.role}' failed: {refreshed.get('error', '')}",
                c.MEDIA_PROVIDER_ERROR,
            )
        asset_entity = MediaAsset(
            id=refreshed.get("id", refreshed.get("role", "")),
            title=_asset_title(refreshed.get("role", "")),
            role=refreshed.get("role", ""),
            provider=refreshed.get("provider", "magnific"),
            status=refreshed.get("status", ""),
            image_url=refreshed.get("image_url", ""),
            alt_text=refreshed.get("alt_text", ""),
            caption=refreshed.get("caption", ""),
            prompt=refreshed.get("prompt", ""),
            error_message=refreshed.get("error", ""),
        )
        return ActionResult.success(asset_entity, f"'{params.role}' regenerated successfully.")

    await ctx.background_task(work(), long_running=True, name="media-studio-regenerate")
    return ActionResult.success(
        MediaAsset(id=target.get("role", ""), title=_asset_title(target.get("role", "")),
                   role=target.get("role", ""),
                   provider=target.get("provider", "magnific"), status="generating"),
        f"Regenerating '{params.role}'. I'll message you when it's ready.",
    )


@chat.function(
    "update_asset_meta",
    "Edit the alt text and/or caption of one asset within a media package, "
    "without regenerating the image itself.",
    action_type="write",
    data_model=MediaAsset,
    event="media-studio.update_asset_meta",
    effects=["update:media_package"],
)
async def update_asset_meta(ctx, params: UpdateAssetMetaParams) -> ActionResult:
    """Edit one asset's alt text and/or caption without regenerating the image."""
    row = await st.get_package(ctx, params.package_id)
    if row is None:
        return _error(
            f"No media package found with id '{params.package_id}'.",
            c.MEDIA_PACKAGE_NOT_FOUND,
        )
    assets = list(row.get("assets", []))
    target = next((a for a in assets if a.get("role") == params.role), None)
    if target is None:
        return _error(
            f"No asset '{params.role}' in package '{params.package_id}'.",
            c.MEDIA_ASSET_NOT_FOUND,
        )
    if params.alt_text.strip():
        target["alt_text"] = params.alt_text.strip()
    if params.caption.strip():
        target["caption"] = params.caption.strip()
    await st.update_package(ctx, params.package_id, {"assets": assets})
    asset_entity = MediaAsset(
        id=target.get("id", target.get("role", "")),
        title=_asset_title(target.get("role", "")),
        role=target.get("role", ""),
        provider=target.get("provider", "magnific"),
        status=target.get("status", ""),
        image_url=target.get("image_url", ""),
        alt_text=target.get("alt_text", ""),
        caption=target.get("caption", ""),
        prompt=target.get("prompt", ""),
        error_message=target.get("error", ""),
    )
    return ActionResult.success(asset_entity, f"Updated alt text/caption for '{params.role}'.")


@chat.function(
    "delete_media_package",
    "Permanently delete a media package and all of its asset records. Does "
    "not delete images already hosted on Magnific's own servers -- only the "
    "package record inside Media Studio.",
    action_type="write",
    data_model=DeleteResult,
    event="media-studio.delete_media_package",
    effects=["delete:media_package"],
)
async def delete_media_package(ctx, params: DeleteMediaPackageParams) -> ActionResult:
    """Permanently delete a media package and all of its assets."""
    deleted = await st.delete_package(ctx, params.package_id)
    if not deleted:
        return _error(
            f"No media package found with id '{params.package_id}'.",
            c.MEDIA_PACKAGE_NOT_FOUND,
        )
    return ActionResult.success(
        DeleteResult(id=params.package_id, title=f"Package {params.package_id}", deleted=True),
        f"Deleted media package '{params.package_id}'.",
    )

"""Chat-function tools.

WHY GENERATION *TRIES* TO RUN IN THE BACKGROUND.

A package with N images means N sequential Mystic jobs (create + poll each
one), and Mystic generation + polling can easily exceed the 30s default
ctx.http timeout even for a single image, let alone several. So the tool
first tries `ctx.background_task(work())`: return an immediate
acknowledgement, and the platform auto-delivers `work()`'s returned
ActionResult as a fresh chat message when it finishes.

`background=True` on the decorator is ONLY advisory metadata (confirmed by
reading imperal_sdk 5.9.12/13 source -- there is no automatic wrapping); the
actual detachment is the explicit `ctx.background_task(...)` call below.

WHY THERE IS A SYNCHRONOUS FALLBACK. `ctx.background_task` raises
RuntimeError when the Context has no kernel-injected spawn hook -- and this
was confirmed to happen not just from a bare dev/terminal session but also
from a real published app invoked from a genuine panel button click. Rather
than surface an unrecoverable error to the user every time that happens,
follow the proven pattern from SEO Audit Engine's `audit_sites`: catch
exactly (RuntimeError, AttributeError) -- the two documented/plausible
spawn-hook-missing shapes -- and simply `await work()` synchronously instead.
A 1-3 image package comfortably fits Mystic's per-image generation+poll time
within the platform's request budget, so this is a legitimate execution path,
not a hack. Any OTHER exception type must keep propagating/roll back as
before -- it means something inside work() itself broke, which is a real bug
and must not be mistaken for "background jobs aren't available".
"""

from __future__ import annotations

from imperal_sdk import ActionResult, sdl

import codes as c
import image_dims
import magnific_client as mc
import model_registry as mr
import storage as st
from app import chat, ext
from models import (
    CreateMediaBriefParams,
    DeleteMediaPackageParams,
    DeleteResult,
    GenerateMediaPackageParams,
    GenerateAssetUpscaleParams,
    GetMediaPackageParams,
    ListMediaPackagesParams,
    MediaAsset,
    MediaPackage,
    RegenerateAssetParams,
    UpdateAssetMetaParams,
)
from shared import (
    ASPECT_RATIO_4_3,
    TEXT_POLICY_ALLOW_TEXT,
    TEXT_POLICY_NO_TEXT,
    VALID_TEXT_POLICIES,
    contains_non_english_text,
    default_alt_text,
    error as _error,
    filename_for_asset,
    is_image_url_expired,
    is_valid_model,
    is_valid_model_choice,
    prompt_for_role,
    roles_for,
    valid_model_choices_hint,
)


def _asset_title(role: str) -> str:
    return "Featured image" if role == "featured" else f"Inline image ({role})"


def _resolve_asset_model(role: str, chosen: str, prompt: str, style_direction: str) -> str:
    """Turn what the caller asked for into the CONCRETE model id stored on
    the asset. \"auto\" is resolved HERE, once, at brief-creation time --
    never re-resolved silently later -- so the stored value is always the
    real, visible, overridable choice (mirrors model_registry's own
    transparency goal: no hidden re-picking on every regenerate).

    A Mystic sub-style (or \"\") is passed through unchanged -- those are
    Mystic's OWN style knob, not a competing model id, so `mc.generate_image`
    keeps handling them exactly as before.
    """
    if chosen == "auto":
        return mr.pick_model(role, prompt, style_direction)
    return chosen


async def _generate_asset_image(ctx, api_key: str, asset: dict, *, on_progress=None) -> str:
    """Route one asset's generation to the right client call based on its
    stored `model`. Empty string or a Mystic sub-style (see shared.MYSTIC_MODELS)
    -> the original Mystic-only path (byte-for-byte, so every existing test
    and behaviour is unchanged) -- now with the pipeline-wide 4:3 landscape
    aspect ratio (shared.ASPECT_RATIO_4_3) forwarded on every call, since
    every blogpost image must be 4:3 landscape (standing directive; Imagen4's
    body builder in model_registry.py carries the same constant). Any OTHER
    registered model id (imagen4-fast, imagen4-ultra, gemini-2.5-flash) ->
    the new generic multi-provider path, whose body is built per-model in
    model_registry.py (Gemini has no aspect_ratio field at all -- documented
    exception, not an oversight).
    """
    model = asset.get("model", "")
    if is_valid_model(model):
        return await mc.generate_image(
            ctx, api_key, asset["prompt"], model=model,
            aspect_ratio=ASPECT_RATIO_4_3, on_progress=on_progress,
        )
    spec = mr.get_model(model)
    try:
        # "sync_base64" models (currently only Classic Fast) answer in ONE
        # call with raw image bytes -- there is no task to create-then-poll,
        # so they get their own client function instead of being forced
        # through generate_image_with_model's create+poll shape.
        if spec.response_kind == "sync_base64":
            return await mc.create_sync_image(ctx, api_key, spec, asset["prompt"])
        return await mc.generate_image_with_model(
            ctx, api_key, asset["prompt"], spec, on_progress=on_progress,
        )
    except mc.ProviderError as exc:
        # Policy: third-party models are always preferred. Magnific's own
        # Mystic is used only after the selected third-party endpoint reports
        # a technical failure, never as an automatic first choice.
        if spec.provider == "magnific":
            raise
        await ctx.log(
            "Third-party Magnific model failed technically; retrying asset with Mystic fallback.",
            level="warning",
        )
        return await mc.generate_image(
            ctx, api_key, asset["prompt"], model="",
            aspect_ratio=ASPECT_RATIO_4_3, on_progress=on_progress,
        )


# WHY AUTO-UPSCALE IS A BEST-EFFORT STEP, NOT A HARD FAILURE.
#
# Standing directive: any generated image smaller than 1500px on its
# smaller side must be auto-upscaled via Magnific's Upscaler Creative
# (images only -- video is explicitly out of scope for this pass). But the
# upscaler is a SEPARATE provider call on top of an already-succeeded
# generation: if the size check or the upscale call itself fails for any
# reason (bad bytes, provider hiccup, unrecognized format), the asset
# already has a perfectly good image -- failing the whole package/asset
# over a size *optimization* would be strictly worse for the user than
# quietly keeping the original. So every failure path here logs a warning
# and returns the ORIGINAL url unchanged; only a clean upscale success
# replaces it.
async def _maybe_upscale_asset_image(ctx, api_key: str, image_url: str) -> dict:
    """Inspect the original generated image and optionally auto-upscale it.

    The returned metadata is deliberately persisted with the asset: the UI
    must show both the untouched provider output and the final upscaled image
    (where present), together with sizes and formats verified from their
    bytes. No size or file type is inferred from a CDN URL.
    """
    result = {
        "image_url": image_url,
        "original_image_url": image_url,
        "original_dimensions": "",
        "original_format": "",
        "original_file_size": "",
        "upscaled_image_url": "",
        "upscaled_dimensions": "",
        "upscaled_format": "",
        "upscaled_file_size": "",
    }
    if not image_url:
        return result
    try:
        original_bytes = await mc.download_image_bytes(image_url)
    except Exception:
        await ctx.log(
            "Could not download the generated image to check its size; "
            "skipping auto-upscale for this asset.", level="warning",
        )
        return result

    dims = image_dims.get_image_dimensions(original_bytes)
    original_format, original_dimensions = image_dims.describe_image(original_bytes)
    result["original_format"] = original_format
    result["original_dimensions"] = original_dimensions
    result["original_file_size"] = image_dims.format_file_size(len(original_bytes))
    if dims is None:
        await ctx.log(
            "Generated image isn't a recognized PNG/JPEG/WebP; skipping "
            "auto-upscale for this asset.", level="warning",
        )
        return result

    width, height = dims
    scale_factor = mc.upscale_scale_factor_for(width, height, min_side=1500)
    if scale_factor is None:
        return result
    try:
        upscaled_url = await mc.upscale_image(ctx, api_key, original_bytes, scale_factor)
        upscaled_bytes = await mc.download_image_bytes(upscaled_url)
    except Exception:
        await ctx.log(
            f"Auto-upscale failed for a {width}x{height} image; keeping the "
            f"original.", level="warning",
        )
        return result

    upscaled_format, upscaled_dimensions = image_dims.describe_image(upscaled_bytes)
    result.update(
        image_url=upscaled_url,
        upscaled_image_url=upscaled_url,
        upscaled_format=upscaled_format,
        upscaled_dimensions=upscaled_dimensions,
        upscaled_file_size=image_dims.format_file_size(len(upscaled_bytes)),
    )
    return result


def _package_to_entity(row: dict) -> MediaPackage:
    assets = [
        MediaAsset(
            id=a.get("id", a.get("role", "")),
            title=_asset_title(a.get("role", "")),
            role=a.get("role", ""),
            provider=a.get("provider", "magnific"),
            model=a.get("model", ""),
            status=a.get("status", "pending"),
            image_url=a.get("image_url", ""),
            original_image_url=a.get("original_image_url", ""),
            original_dimensions=a.get("original_dimensions", ""),
            original_format=a.get("original_format", ""),
            original_file_size=a.get("original_file_size", ""),
            upscaled_image_url=a.get("upscaled_image_url", ""),
            upscaled_dimensions=a.get("upscaled_dimensions", ""),
            upscaled_format=a.get("upscaled_format", ""),
            upscaled_file_size=a.get("upscaled_file_size", ""),
            filename=a.get("filename", ""),
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
        text_policy=row.get("text_policy", "no_text"),
        image_text=row.get("image_text", ""),
        status=row.get("status", "draft"),
        model=row.get("model", ""),
        lang=row.get("lang", ""),
        native_title=row.get("native_title", ""),
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
    model_choice = params.model.strip()
    if not is_valid_model_choice(model_choice):
        return _error(
            f"'{model_choice}' isn't a model Media Hub knows. "
            f"Use one of: {valid_model_choices_hint()}",
            c.MEDIA_INVALID_MODEL,
        )
    text_policy = (params.text_policy.strip() or "no_text").lower()
    if text_policy not in VALID_TEXT_POLICIES:
        return _error(
            f"'{params.text_policy}' isn't a text_policy Media Hub knows. "
            f"Use one of: {', '.join(VALID_TEXT_POLICIES)}",
            c.MEDIA_INVALID_TEXT_POLICY,
        )
    image_text = params.image_text.strip()
    if text_policy == TEXT_POLICY_ALLOW_TEXT and not image_text:
        return _error(
            "text_policy='allow_text' needs the actual words to render -- "
            "pass them in image_text (e.g. a price, a short label, a one-line "
            "phrase). A prompt can never just say 'maybe some text': it must "
            "say either 'no text' or the exact text.",
            c.MEDIA_INVALID_TEXT_POLICY,
        )
    if text_policy == TEXT_POLICY_NO_TEXT:
        image_text = ""

    non_english = contains_non_english_text(
        params.article_title, params.summary, params.style_direction,
    )
    if non_english:
        return _error(
            "Image prompts must be written in English -- Magnific Mystic is "
            f"tuned for English input. Found non-English text: '{non_english[:40]}'. "
            "Translate article_title/summary/style_direction to English "
            "before creating this brief (the article itself can stay in "
            "any language -- only the image brief needs English).",
            c.MEDIA_PROMPT_NOT_ENGLISH,
        )

    roles = roles_for(params.inline_count)
    assets = []
    for role in roles:
        prompt = prompt_for_role(
            role, params.article_title, params.summary, params.style_direction,
            params.lang.strip(), text_policy, image_text,
        )
        resolved_model = _resolve_asset_model(
            role, model_choice, prompt, params.style_direction,
        )
        provider = mr.get_model(resolved_model).provider if resolved_model in mr.MODELS else "magnific"
        assets.append({
            "id": role,
            "role": role,
            "provider": provider,
            "model": resolved_model,
            "status": "pending",
            "image_url": "",
            "filename": filename_for_asset(params.article_title or params.summary, role),
            "alt_text": "",
            "caption": "",
            "prompt": prompt,
            "error": "",
        })

    package_id, row = await st.create_package(ctx, {
        "site": params.site,
        "article_title": params.article_title,
        "summary": params.summary,
        "style_direction": params.style_direction,
        "text_policy": text_policy,
        "image_text": image_text,
        "status": "draft",
        "model": model_choice,
        "lang": params.lang.strip().lower(),
        "native_title": params.native_title.strip(),
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
    """Generate every pending asset in a package via Magnific, in the background.

    WHY "ready" ALONE ISN'T ENOUGH TO SKIP AN ASSET. Confirmed live on a real
    package: Magnific/Freepik's CDN image URL carries a signed token that
    expires a few hours after generation, but `status` is written once and
    never re-checked -- so an asset generated hours ago is still stored as
    "ready" while its `image_url` is already a dead link ("Image
    unavailable" in the panel). Before this fix the loop below skipped every
    "ready" asset unconditionally, which is why "Generate all"/"Regenerate"
    looked like they did nothing on an old package: everything was already
    (stale-)"ready", so there was nothing left to (re)generate. Now a
    "ready" asset is only skipped when its stored URL is NOT expired
    (shared.is_image_url_expired) -- an expired one is regenerated exactly
    like a pending/failed one.
    """
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
            "No Magnific API key connected yet. Open Media Hub settings "
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
            if asset.get("status") == "ready" and not is_image_url_expired(asset.get("image_url", "")):
                continue
            try:
                await ctx.progress(
                    (i / total) * 100,
                    f"Generating {asset.get('role', 'image')}...",
                )
                generated_url = await _generate_asset_image(ctx, api_key, asset)
                asset.update(await _maybe_upscale_asset_image(ctx, api_key, generated_url))
                asset["status"] = "ready"
                asset["error"] = ""
                if not asset.get("alt_text"):
                    lang = current.get("lang", "")
                    display_title = current.get("native_title") or current.get("article_title", "")
                    asset["alt_text"] = default_alt_text(
                        asset["role"], display_title, lang,
                    )
                if not asset.get("caption"):
                    lang = current.get("lang", "")
                    display_title = current.get("native_title") or current.get("article_title", "")
                    asset["caption"] = default_alt_text(
                        asset["role"], display_title, lang,
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

    coro = work()
    try:
        await ctx.background_task(coro, long_running=True, name="media-studio-generate")
    except (RuntimeError, AttributeError) as exc:
        # No kernel spawn hook available in this context (dev/terminal
        # session, or a panel dispatch path that doesn't wire one up).
        # Same discipline as SEO Audit Engine's audit_sites: run the same
        # coroutine synchronously instead of failing the whole request.
        await ctx.log(f"generate_media_package: no background spawn hook ({exc}); running synchronously", level="warning")
        return await coro

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
            "No Magnific API key connected yet. Open Media Hub settings "
            "and paste your Magnific API key first.",
            c.MEDIA_KEY_NOT_CONFIGURED,
        )

    if params.prompt_override.strip():
        non_english = contains_non_english_text(params.prompt_override)
        if non_english:
            return _error(
                "Image prompts must be written in English -- Magnific "
                f"Mystic is tuned for English input. Found non-English "
                f"text: '{non_english[:40]}'. Translate prompt_override to "
                "English first.",
                c.MEDIA_PROMPT_NOT_ENGLISH,
            )
        target["prompt"] = params.prompt_override.strip()
    override_model = params.model.strip()
    if override_model and not is_valid_model_choice(override_model):
        return _error(
            f"'{override_model}' isn't a model Media Hub knows. "
            f"Use one of: {valid_model_choices_hint()}",
            c.MEDIA_INVALID_MODEL,
        )
    if override_model:
        resolved_model = _resolve_asset_model(
            params.role, override_model, target["prompt"],
            row.get("style_direction", ""),
        )
        target["model"] = resolved_model
        target["provider"] = (
            mr.get_model(resolved_model).provider
            if resolved_model in mr.MODELS else "magnific"
        )
    if not target.get("filename"):
        # Backfills a SEO/AEO filename for packages created before this
        # field existed -- otherwise a regenerate on an old package would
        # keep silently producing a provider-raw filename downstream.
        target["filename"] = filename_for_asset(
            row.get("article_title") or row.get("summary", ""), params.role,
        )
    target["status"] = "generating"
    await st.update_package(ctx, params.package_id, {"assets": assets})

    async def work() -> ActionResult:
        try:
            generated_url = await _generate_asset_image(ctx, api_key, target)
            target.update(await _maybe_upscale_asset_image(ctx, api_key, generated_url))
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
            model=refreshed.get("model", ""),
            status=refreshed.get("status", ""),
            image_url=refreshed.get("image_url", ""),
            filename=refreshed.get("filename", ""),
            alt_text=refreshed.get("alt_text", ""),
            caption=refreshed.get("caption", ""),
            prompt=refreshed.get("prompt", ""),
            error_message=refreshed.get("error", ""),
        )
        return ActionResult.success(asset_entity, f"'{params.role}' regenerated successfully.")

    coro = work()
    try:
        await ctx.background_task(coro, long_running=True, name="media-studio-regenerate")
    except (RuntimeError, AttributeError) as exc:
        # Same fallback discipline as generate_media_package: no kernel
        # spawn hook here, so just run the same coroutine synchronously.
        await ctx.log(f"regenerate_asset: no background spawn hook ({exc}); running synchronously", level="warning")
        return await coro

    return ActionResult.success(
        MediaAsset(id=target.get("role", ""), title=_asset_title(target.get("role", "")),
                   role=target.get("role", ""),
                   provider=target.get("provider", "magnific"), model=target.get("model", ""),
                   status="generating"),
        f"Regenerating '{params.role}'. I'll message you when it's ready.",
    )


@chat.function(
    "generate_asset_upscale",
    "Create a larger version of one generated image with Magnific Creative Upscaler. "
    "Choose one of Magnific's supported scale factors, then retrieve the updated asset.",
    action_type="write",
    background=True,
    long_running=True,
    data_model=MediaAsset,
    event="media-studio.generate_asset_upscale",
    effects=["update:media_package"],
)
async def generate_asset_upscale(ctx, params: GenerateAssetUpscaleParams) -> ActionResult:
    """Upscale one asset from its preserved original provider image.

    Manual upscale never replaces the original source URL. It only updates the
    separate `upscaled_*` fields and points `image_url` at the newest output,
    allowing the card to show an honest original-versus-upscaled pair.
    """
    row = await st.get_package(ctx, params.package_id)
    if row is None:
        return _error(f"No media package found with id '{params.package_id}'.", c.MEDIA_PACKAGE_NOT_FOUND)
    assets = list(row.get("assets", []))
    target = next((asset for asset in assets if asset.get("role") == params.role), None)
    if target is None:
        return _error(
            f"No asset '{params.role}' in package '{params.package_id}'.", c.MEDIA_ASSET_NOT_FOUND,
        )

    scale_factor = params.scale_factor.strip().lower()
    if scale_factor not in mc.available_upscale_scale_factors():
        choices = ", ".join(mc.available_upscale_scale_factors())
        return _error(f"Magnific supports these upscale values: {choices}.", c.MEDIA_PROVIDER_ERROR)

    original_url = target.get("original_image_url") or target.get("image_url", "")
    if not original_url:
        return _error("Generate this image before creating a larger version.", c.MEDIA_ASSET_NOT_FOUND)
    if is_image_url_expired(original_url):
        return _error("This image link has expired. Regenerate the image first, then upscale it.", c.MEDIA_PROVIDER_ERROR)

    api_key = await ctx.secrets.get("magnific_api_key")
    if not api_key:
        return _error("No Magnific API key connected yet. Open Media Hub settings and paste your Magnific API key first.", c.MEDIA_KEY_NOT_CONFIGURED)

    async def work() -> ActionResult:
        try:
            await ctx.progress(10, "Preparing image for upscale...")
            original_bytes = await mc.download_image_bytes(original_url)
            await ctx.progress(30, f"Creating {scale_factor} upscale...")
            upscaled_url = await mc.upscale_image(ctx, api_key, original_bytes, scale_factor)
            upscaled_bytes = await mc.download_image_bytes(upscaled_url)
            upscaled_format, upscaled_dimensions = image_dims.describe_image(upscaled_bytes)
            original_format, original_dimensions = image_dims.describe_image(original_bytes)
            target.update(
                image_url=upscaled_url,
                original_image_url=original_url,
                original_format=target.get("original_format") or original_format,
                original_dimensions=target.get("original_dimensions") or original_dimensions,
                original_file_size=(target.get("original_file_size")
                                    or image_dims.format_file_size(len(original_bytes))),
                upscaled_image_url=upscaled_url,
                upscaled_format=upscaled_format,
                upscaled_dimensions=upscaled_dimensions,
                upscaled_file_size=image_dims.format_file_size(len(upscaled_bytes)),
            )
            current = await st.update_package(ctx, params.package_id, {"assets": assets})
            refreshed = next((asset for asset in current.get("assets", []) if asset.get("role") == params.role), target)
            return ActionResult.success(_package_to_entity({**row, "assets": [refreshed]}).assets[0], f"Created a {scale_factor} upscaled version of '{params.role}'.")
        except mc.ProviderError as exc:
            return _error(f"Could not upscale '{params.role}': {exc}", c.MEDIA_PROVIDER_ERROR)

    coro = work()
    try:
        await ctx.background_task(coro, long_running=True, name="media-studio-upscale")
    except (RuntimeError, AttributeError) as exc:
        await ctx.log(f"generate_asset_upscale: no background spawn hook ({exc}); running synchronously", level="warning")
        return await coro
    return ActionResult.success(
        MediaAsset(id=target.get("role", ""), title=_asset_title(target.get("role", "")), role=target.get("role", ""), status="generating"),
        f"Creating a {scale_factor} upscaled version of '{params.role}'. I'll message you when it's ready.",
    )


@chat.function(
    "update_asset_meta",
    "Edit the image title, description, alt text, and/or caption of one asset "
    "without regenerating the image itself.",
    action_type="write",
    data_model=MediaAsset,
    event="media-studio.update_asset_meta",
    effects=["update:media_package"],
)
async def update_asset_meta(ctx, params: UpdateAssetMetaParams) -> ActionResult:
    """Edit publish metadata without regenerating; description is reused on regeneration."""
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
    if params.image_title.strip():
        target["filename"] = params.image_title.strip()
    if params.image_description.strip():
        description = params.image_description.strip()
        non_english = contains_non_english_text(description)
        if non_english:
            return _error(
                "Image description must be written in English -- Magnific Mystic is tuned "
                f"for English input. Found non-English text: '{non_english[:40]}'.",
                c.MEDIA_PROMPT_NOT_ENGLISH,
            )
        # `prompt` is the canonical pipeline field: generation, regeneration,
        # exports and the card all read this same value.
        target["prompt"] = description
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
        filename=target.get("filename", ""),
        alt_text=target.get("alt_text", ""),
        caption=target.get("caption", ""),
        prompt=target.get("prompt", ""),
        error_message=target.get("error", ""),
    )
    return ActionResult.success(asset_entity, f"Updated metadata for '{params.role}'.")


@chat.function(
    "delete_media_package",
    "Permanently delete a media package and all of its asset records. Does "
    "not delete images already hosted on Magnific's own servers -- only the "
    "package record inside Media Hub.",
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

"""Chat tools + the monthly schedule for the Image Prompt engine.

WHY THIS IS ITS OWN FILE, NOT FOLDED INTO handlers.py.

Same reasoning as handlers_discovery.py: this is another place where the
extension acts WITHOUT a human -- reviewing its own generated-prompt quality
and watching vendor prompting guides once a month, and writing a permanent
log entry every time, whether or not anything changed. Keeping it visible on
its own, next to the model-discovery schedule it shares this extension with.
"""

from __future__ import annotations

import time

from imperal_sdk import ActionResult, sdl

import codes as c
import prompt_engine as pe
from app import chat, ext
from models import (
    AnalyzePromptParams,
    CheckPromptEngineUpdatesParams,
    FixedPrompt,
    FixPromptParams,
    ListPromptEngineLogParams,
    PromptAnalysis,
    PromptEngineConfig,
    PromptEngineLogEntry,
    PromptEngineReviewResult,
    SavePromptEngineConfigParams,
)
from shared import error as _error


def _fmt_ts(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ts))


@chat.function(
    "analyze_prompt",
    "Score an image prompt against a 6-slot rubric (subject, environment, "
    "composition, lighting, style, technical/camera) -- a keyword-presence "
    "check, not a quality judgement. Shows exactly which slots the prompt "
    "does not mention yet.",
    action_type="read",
    data_model=PromptAnalysis,
    event="media-studio.analyze_prompt",
)
async def analyze_prompt(ctx, params: AnalyzePromptParams) -> ActionResult:
    """Analyze one prompt against the 6-slot rubric."""
    if not params.prompt.strip():
        return _error(
            "Nothing to analyze -- pass the prompt text in `prompt`.",
            c.MEDIA_PROMPT_EMPTY,
        )
    result = pe.analyze_prompt(params.prompt)
    entity = PromptAnalysis(
        id="analysis", title=f"Prompt analysis -- score {result['score']}/100",
        prompt=params.prompt, score=result["score"],
        covered_slots=result["covered"], missing_slots=result["missing"],
    )
    headline = (
        f"Score {result['score']}/100 -- covers {len(result['covered'])}/6 "
        f"slots."
        + (f" Missing: {', '.join(result['missing'])}." if result["missing"] else " All 6 slots addressed.")
    )
    return ActionResult.success(entity, headline)


@chat.function(
    "fix_prompt",
    "Deterministically fix a prompt's missing structural slots (lighting, "
    "camera/lens, generic style fallback) without inventing subject or "
    "scene content. Any subject/environment gap that's still missing after "
    "the fix is reported, not guessed -- add real words yourself for those.",
    action_type="read",
    data_model=FixedPrompt,
    event="media-studio.fix_prompt",
)
async def fix_prompt(ctx, params: FixPromptParams) -> ActionResult:
    """Run the deterministic fix pass on one prompt."""
    if not params.prompt.strip():
        return _error(
            "Nothing to fix -- pass the prompt text in `prompt`.",
            c.MEDIA_PROMPT_EMPTY,
        )
    config = await pe.get_prompt_config(ctx)
    fixed, additions, unfixable = pe.fix_prompt(params.prompt, params.role or "featured", config)
    entity = FixedPrompt(
        id="fix", title="Fixed prompt", original_prompt=params.prompt,
        fixed_prompt=fixed, additions=additions, unfixable_issues=unfixable,
    )
    if additions:
        headline = f"Added {len(additions)} clause(s): {'; '.join(additions)}."
    else:
        headline = "Nothing to add -- all fixable slots were already covered."
    if unfixable:
        headline += f" Still missing (needs real content): {', '.join(unfixable)}."
    return ActionResult.success(entity, headline)


async def _run_review(ctx) -> PromptEngineReviewResult:
    """The actual monthly review, shared by the manual tool and the tick."""
    result = await pe.run_self_review(ctx)
    await pe.record_review(ctx, result)
    return PromptEngineReviewResult(
        id=f"review-{int(result['checked_at'])}",
        title=f"Prompt engine review {_fmt_ts(result['checked_at'])}",
        checked_at=_fmt_ts(result["checked_at"]),
        guides_checked=result["guides_checked"],
        guides_changed=result["guides_changed"],
        guides_unreachable=result["guides_unreachable"],
        sample_size=result["sample_size"],
        avg_score=result["avg_score"] or 0,
        review_recommended=result["review_recommended"],
        note=result["note"],
    )


@chat.function(
    "check_prompt_engine_updates",
    "Manually run the Image Prompt engine's self-review right now (same "
    "check that also runs automatically once a month): hash-diffs the "
    "vendor prompting guides this engine is built from, and re-scores a "
    "sample of this app's own recently generated prompts. Always records "
    "the result, whether or not anything changed -- never silently "
    "rewrites the engine's own rules.",
    action_type="read",
    data_model=PromptEngineReviewResult,
    event="media-studio.check_prompt_engine_updates",
)
async def check_prompt_engine_updates(
    ctx, params: CheckPromptEngineUpdatesParams,
) -> ActionResult:
    """Run the prompt-engine self-review right now and report what it found."""
    result = await _run_review(ctx)
    return ActionResult.success(result, result.note)


@chat.function(
    "list_prompt_engine_log",
    "Show the history of monthly Image Prompt engine self-reviews -- every "
    "past run, whether or not it found anything, newest first.",
    action_type="read",
    data_model=PromptEngineLogEntry,
    event="media-studio.list_prompt_engine_log",
)
async def list_prompt_engine_log(
    ctx, params: ListPromptEngineLogParams,
) -> ActionResult:
    """List past self-review runs, newest first."""
    rows = await pe.list_review_log(ctx, limit=params.limit)
    entries = [
        PromptEngineLogEntry(
            id=f"{r.get('checked_at', 0)}",
            title=_fmt_ts(r.get("checked_at", 0)),
            checked_at=_fmt_ts(r.get("checked_at", 0)),
            guides_changed=list(r.get("guides_changed") or []),
            guides_unreachable=list(r.get("guides_unreachable") or []),
            sample_size=int(r.get("sample_size") or 0),
            avg_score=int(r.get("avg_score") or 0),
            review_recommended=bool(r.get("review_recommended")),
            note=str(r.get("note", "")),
        )
        for r in rows
    ]
    return ActionResult.success(
        sdl.EntityList(items=entries),
        f"{len(entries)} past review(s) on record.",
    )


@ext.schedule("media_prompt_engine_review", pe.TICK_CRON)
async def media_prompt_engine_review(ctx) -> None:
    """Wakes up hourly, asks 'already reviewed this calendar month?', and
    almost always goes back to sleep -- same alarm-clock shape as
    media_model_discovery, offset by 10 minutes so the two never collide.
    Shares `_run_review` with the manual tool so there is exactly one code
    path (and exactly one log write) for a review, whichever way it fires."""
    if not await pe.due(ctx):
        return
    result = await _run_review(ctx)
    if result.review_recommended:
        await ctx.log(
            f"prompt engine review: recommended -- {result.note}", "info",
        )


@chat.function(
    "get_prompt_engine_config",
    "Read the Image Prompt engine's current editable settings (generic "
    "lighting/camera/style fallback clauses and the review alert "
    "threshold) -- what App settings > Image Prompt engine shows.",
    action_type="read",
    data_model=PromptEngineConfig,
    event="media-studio.get_prompt_engine_config",
)
async def get_prompt_engine_config(ctx, params: CheckPromptEngineUpdatesParams) -> ActionResult:
    """Read the effective (saved-or-default) prompt engine config."""
    config = await pe.get_prompt_config(ctx)
    entity = PromptEngineConfig(
        id="prompt-engine-config", title="Image Prompt engine settings",
        generic_lighting=config["generic_lighting"],
        generic_camera_featured=config["generic_camera_featured"],
        generic_camera_inline=config["generic_camera_inline"],
        generic_style=config["generic_style"],
        score_alert_threshold=int(config["score_alert_threshold"]),
    )
    return ActionResult.success(entity, "Current Image Prompt engine settings.")


@chat.function(
    "save_prompt_engine_config",
    "Apply edited Image Prompt engine settings from the App settings > "
    "Image Prompt engine tab: the generic lighting/camera/style fallback "
    "clauses this engine appends when a prompt is missing them, and the "
    "review alert threshold. A blank field keeps its current value -- "
    "nothing is ever blanked out by omission. Takes effect immediately, "
    "on the very next brief/fix/review, no redeploy needed.",
    action_type="write",
    chain_callable=True,
    data_model=PromptEngineConfig,
    event="media-studio.save_prompt_engine_config",
    effects=["media-studio.prompt_engine_config.updated"],
)
async def save_prompt_engine_config(
    ctx, params: SavePromptEngineConfigParams,
) -> ActionResult:
    """Merge and persist the Image Prompt engine's editable settings."""
    updates: dict = {
        "generic_lighting": params.generic_lighting.strip(),
        "generic_camera_featured": params.generic_camera_featured.strip(),
        "generic_camera_inline": params.generic_camera_inline.strip(),
        "generic_style": params.generic_style.strip(),
    }
    threshold_raw = params.score_alert_threshold.strip()
    if threshold_raw:
        try:
            threshold = int(threshold_raw)
        except ValueError:
            return _error(
                f"'{threshold_raw}' isn't a whole number -- "
                "score_alert_threshold must be 0-100.",
                c.MEDIA_PROMPT_CONFIG_INVALID,
            )
        if not (0 <= threshold <= 100):
            return _error(
                "score_alert_threshold must be between 0 and 100.",
                c.MEDIA_PROMPT_CONFIG_INVALID,
            )
        updates["score_alert_threshold"] = threshold

    config = await pe.save_prompt_config(ctx, updates)
    entity = PromptEngineConfig(
        id="prompt-engine-config", title="Image Prompt engine settings",
        generic_lighting=config["generic_lighting"],
        generic_camera_featured=config["generic_camera_featured"],
        generic_camera_inline=config["generic_camera_inline"],
        generic_style=config["generic_style"],
        score_alert_threshold=int(config["score_alert_threshold"]),
    )
    return ActionResult.success(entity, "Image Prompt engine settings applied.")

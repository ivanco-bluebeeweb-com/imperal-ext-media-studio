"""Chat tools + the daily schedule for new-model discovery.

WHY THIS IS ITS OWN FILE, NOT FOLDED INTO handlers.py.

Same reasoning as SEO Audit Engine's handlers_schedule.py: this is the one
place where the extension acts WITHOUT a human -- it reaches out to
Magnific's own docs site on its own schedule and writes a permanent log
entry every time, whether or not it found anything. That is worth keeping
visible on its own, not buried among the media-generation tools.
"""

from __future__ import annotations

import time

from imperal_sdk import ActionResult, sdl

import codes as c
import model_discovery as md
import model_registry as mr
from app import chat, ext
from models import (
    CheckNewModelsParams,
    ListModelDiscoveryLogParams,
    ModelDiscoveryFinding,
    ModelDiscoveryLogEntry,
    ModelDiscoveryResult,
)
from shared import error as _error


def _fmt_ts(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ts))


async def _run_check(ctx) -> ModelDiscoveryResult:
    """The actual check, shared by the manual tool and the daily tick."""
    now = time.time()
    ts_label = _fmt_ts(now)
    try:
        new_slugs = await md.find_new_models(ctx)
    except Exception as exc:
        await md.record_check(ctx, found=[], result=f"fetch_failed: {exc}", ts=now)
        return ModelDiscoveryResult(
            id=f"discovery-{int(now)}",
            title=f"Model check {ts_label} -- could not reach docs",
            checked_at=ts_label,
            source_reachable=False,
            known_model_count=len(mr.MODELS),
            new_candidates=[],
            note=(
                "Could not reach Magnific's docs sitemap to check for new "
                f"models: {exc}. Nothing was added or changed."
            ),
        )

    result = "found_new" if new_slugs else "no_new"
    await md.record_check(ctx, found=new_slugs, result=result, ts=now)

    findings = [
        ModelDiscoveryFinding(
            id=slug,
            title=slug,
            slug=slug,
            docs_url=f"https://docs.magnific.com/api-reference/text-to-image/{slug}",
        )
        for slug in new_slugs
    ]
    if new_slugs:
        note = (
            f"{len(new_slugs)} docs page(s) found that aren't in the model "
            "registry yet. A docs page existing does NOT mean the model is "
            "usable yet -- its exact request fields still need to be read "
            "off its own page and added to model_registry.py by hand, the "
            "same way every current model was confirmed. This is recorded "
            "so it is never silently missed."
        )
    else:
        note = "No new text-to-image models found beyond what's already registered."

    return ModelDiscoveryResult(
        id=f"discovery-{int(now)}",
        title=f"Model check {ts_label} -- {'new candidate(s) found' if new_slugs else 'nothing new'}",
        checked_at=ts_label,
        source_reachable=True,
        known_model_count=len(mr.MODELS),
        new_candidates=findings,
        note=note,
    )


@chat.function(
    "check_new_models",
    "Check Magnific's own docs site right now for text-to-image models we "
    "don't support yet. Always records the result, whether or not anything "
    "new is found -- this is the same check that also runs automatically "
    "once a day.",
    action_type="read",
    data_model=ModelDiscoveryResult,
    event="media-studio.check_new_models",
)
async def check_new_models(ctx, params: CheckNewModelsParams) -> ActionResult:
    """Manually run the new-model check right now (same check the daily
    schedule runs) and report what it found."""
    result = await _run_check(ctx)
    if not result.source_reachable:
        return _error(result.note, c.MEDIA_PROVIDER_ERROR)
    headline = (
        f"Checked -- {result.known_model_count} models known. {result.note}"
    )
    return ActionResult.success(result, headline)


@chat.function(
    "list_model_discovery_log",
    "Show the history of daily new-model checks -- every past run, whether "
    "or not it found anything, newest first.",
    action_type="read",
    data_model=ModelDiscoveryLogEntry,
    event="media-studio.list_model_discovery_log",
)
async def list_model_discovery_log(ctx, params: ListModelDiscoveryLogParams) -> ActionResult:
    """List past new-model check runs, newest first, whether or not each
    one found anything -- so a run is never silently invisible."""
    rows = await md.list_log(ctx, limit=params.limit)
    entries = [
        ModelDiscoveryLogEntry(
            id=f"{r.get('checked_at', 0)}",
            title=_fmt_ts(r.get("checked_at", 0)),
            checked_at=_fmt_ts(r.get("checked_at", 0)),
            source_reachable=not str(r.get("result", "")).startswith("fetch_failed"),
            new_candidate_slugs=list(r.get("found") or []),
            note=str(r.get("result", "")),
        )
        for r in rows
    ]
    return ActionResult.success(
        sdl.EntityList(items=entries),
        f"{len(entries)} past check(s) on record.",
    )


@ext.schedule("media_model_discovery", md.TICK_CRON)
async def media_model_discovery(ctx) -> None:
    """Wakes up hourly, asks 'already checked today?', and almost always
    goes back to sleep -- identical shape to SEO Audit Engine's own
    scheduled-audit alarm clock (schedule_settings.due/mark_ran)."""
    if not await md.due(ctx):
        return
    result = await _run_check(ctx)
    if result.new_candidates:
        slugs = ", ".join(f.slug for f in result.new_candidates)
        await ctx.log(f"model discovery: new candidate docs page(s): {slugs}", "info")

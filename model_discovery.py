"""Daily check for new Magnific text-to-image models we don't support yet.

WHY THIS CHECKS THE SITEMAP, NOT `docs.magnific.com/llms.txt`.

`llms.txt` looked like the obvious machine-readable source, but reading it
end-to-end (2026-08-11) proved it is a CURATED subset, not a full index --
it does not even mention Imagen 4 (Fast/Ultra), which is a real, confirmed,
already-integrated model. Trusting it would make this checker blind to
real models. `sitemap.xml` is Magnific's own generated site index and is
what actually caught every model added to `model_registry.py` today
(Nano Banana Pro/Flash, Flux 2 Flex/Klein, RunWay Text-to-Image, etc.), so
it is the source of truth here.

WHY THIS NEVER AUTO-WRITES A NEW ModelSpec ROW.

`model_registry.py`'s whole discipline is: a row only exists once its exact
REST path AND request-body field names were read off that model's own docs
page -- never guessed from a URL pattern (see that file's "WHY 2026-08-11's
EXPANSION" note for a real example of a guessed GPT Image path that 404s).
A sitemap URL only proves a docs PAGE exists; it proves nothing about the
body shape. So this checker's job stops at "here is a new page you don't
have a model for yet" -- turning that into a real adapter is the same
manual-confirmation step used for every model already in the registry, not
something this checker can respect and still be honest.

WHAT COUNTS AS "NEW".

Only sitemap URLs under `/api-reference/text-to-image/` are candidates --
that is the same path prefix every text-to-image model in the registry
lives under (Gemini 2.5 Flash is the one documented exception, already
registered). A URL only becomes a genuinely reportable finding if BOTH:
  (a) it looks like a model's own `overview` or `generate`/`post-*` page
      (not a `-task`/`-tasks`/`get-*-task`/`task-by-id` status-only page,
      which never introduces a new model by itself), AND
  (b) its slug does not match ANY create_path already in MODELS.
This mirrors the same "don't invent, don't miss" discipline as the model
rows themselves.
"""

from __future__ import annotations

import re
import time

import model_registry as mr

SITEMAP_URL = "https://docs.magnific.com/sitemap.xml"

#: Where the running history of checks lives -- one doc per day, kept
#: forever (never overwritten), because the user explicitly asked that a
#: new model being found and added is "обязательно записывать" (must
#: always be recorded), not just flashed in a chat message and forgotten.
DISCOVERY_LOG_COLLECTION = "model_discovery_log"

#: Where "when did we last check" lives -- same due()/mark_ran() shape as
#: SEO Audit Engine's schedule_settings.py, so a slow/failed check can
#: never fire twice in the same day and re-notify about the same finding.
DISCOVERY_STATE_COLLECTION = "model_discovery_state"
DISCOVERY_STATE_KEY = "state"

#: Runs once an hour and asks "already checked today?" -- identical shape
#: to SEO Audit Engine's TICK_CRON. An hourly wake-up costs one store read
#: when it is not yet time; the actual check (one HTTP GET) is cheap enough
#: that, unlike a multi-site SEO audit, there is no need to pick a
#: low-traffic hour -- this never touches the user's own sites.
TICK_CRON = "10 * * * *"

#: Fixed, not user-configurable via a "which hour" setting like SEO Audit
#: Engine -- checking Magnific's docs has no "quiet hours" concern (it is
#: Magnific's server, not a client's), so one daily hour, chosen simply to
#: not collide with the SEO Audit Engine's own tick, is enough.
CHECK_HOUR_UTC = 6

_STATUS_ONLY_SUFFIXES = (
    "-task", "-tasks", "-by-id", "get-{task-id}-by-id",
)

#: Slugs with a real docs page that were EXPLICITLY reviewed and left out on
#: 2026-08-11, not missed. Kept here so the daily check reports them as
#: "known but declined" (see record_check) instead of re-flagging the same
#: reviewed decision as if it were brand new every single day:
#:   - flux-kontext-pro / flux-kontext-max: image-EDIT endpoints that
#:     require an input image, out of scope for a text-to-image brief
#:     (same reasoning as Reimagine Flux / Seedream-*-edit below).
#:   - seedream-v4-edit / seedream-v4-5-edit / seedream-v5-lite-edit /
#:     seedream-v5-pro-edit: the edit counterparts of the seedream models
#:     already registered -- same "requires an input image" exclusion.
#:   - imagen3: confirmed deprecated (past its documented removal date);
#:     imagen4-fast/imagen4-ultra are the registered replacements.
#:   - imagen4: NOT a model of its own -- it is the shared parent
#:     `/overview` page for imagen4-fast and imagen4-ultra, both already
#:     registered separately.
#:   - seedream-4 / seedream-4-edit: folder-name aliases -- these overview
#:     pages sit at .../seedream-4/... but their OWN post-*/get-* pages
#:     underneath are seedream-v4 (registered) and its edit counterpart
#:     (already excluded above). Same model, just a differently-named
#:     containing folder; not a second, undiscovered model.
#:   - reimagine-flux: an image-EDIT endpoint (transforms an existing
#:     image), same "requires an input image" exclusion as the other
#:     edit-only entries above.
#: A slug leaves this set the moment it is actually reviewed and given a
#: real ModelSpec row -- this is a "seen and declined", not a permanent ban.
EXCLUDED_SLUGS = {
    "flux-kontext-pro",
    "flux-kontext-max",
    "seedream-v4-edit",
    "seedream-v4-5-edit",
    "seedream-v5-lite-edit",
    "seedream-v5-pro-edit",
    "imagen3",
    "imagen4",
    "seedream-4",
    "seedream-4-edit",
    "reimagine-flux",
}


def _now_date(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts if ts is not None else time.time()))


def _known_create_slugs() -> set[str]:
    """Every path segment already covered by a registered model, so a new
    sitemap URL can be compared against what we already integrated instead
    of just what got a docs page (which is a much bigger, noisier set)."""
    slugs = set()
    for spec in mr.MODELS.values():
        # e.g. "/v1/ai/text-to-image/nano-banana-pro" -> "nano-banana-pro".
        # "classic-fast"'s own create_path is "/v1/ai/text-to-image" (no
        # per-model segment -- confirmed on its docs page, this IS the base
        # text-to-image path), so its slug must be the model id itself, or
        # every future sitemap scan would misread this row's basename as
        # the generic "text-to-image" and never actually match anything.
        tail = spec.create_path.rstrip("/").rsplit("/", 1)[-1]
        slugs.add(spec.id if tail == "text-to-image" else tail)
    return slugs


def _is_status_only_url(path: str) -> bool:
    tail = path.rstrip("/").rsplit("/", 1)[-1]
    return tail.startswith("get-") and any(
        tail.endswith(suf) for suf in _STATUS_ONLY_SUFFIXES
    ) or tail in _STATUS_ONLY_SUFFIXES


def _candidate_model_slug(loc: str) -> str | None:
    """From one sitemap <loc> URL, the model slug IF this looks like a
    model-introducing page (overview/generate/post-*), else None."""
    m = re.search(r"/api-reference/text-to-image/([^\s<]+)", loc)
    if not m:
        return None
    rest = m.group(1)
    parts = rest.split("/")
    tail = parts[-1]

    if _is_status_only_url(tail):
        return None

    if tail in ("overview", "generate"):
        # e.g. ".../flux-2-flex/overview" -> "flux-2-flex"
        return parts[0] if len(parts) > 1 else None
    if tail.startswith("post-"):
        # e.g. "post-nano-banana-pro" -> "nano-banana-pro"; but also
        # ".../seedream-4/post-seedream-v4" -> prefer the REAL create_path
        # tail (post-seedream-v4), matching how create_path is built in
        # model_registry.py (path basename, not the folder name).
        return tail[len("post-"):]
    return None


async def fetch_candidate_slugs(ctx) -> list[str]:
    """GET the sitemap and return every distinct text-to-image model slug
    that looks like a real model-introducing page. Raises on HTTP failure
    -- callers must not silently treat a fetch failure as "no new models".
    """
    resp = await ctx.http.get(SITEMAP_URL, timeout=30)
    if not (200 <= resp.status_code < 300):
        raise RuntimeError(f"sitemap fetch failed (HTTP {resp.status_code})")
    # HTTPResponse.text is a METHOD (confirmed against imperal_sdk's real
    # types/models.py), not an attribute -- and `.body` may already be a
    # non-str (bytes, or even dict if a proxy mis-detects content-type), so
    # `.text()` is the one call that normalizes all of those to str.
    body = resp.text()
    locs = re.findall(r"<loc>([^<]+)</loc>", body)
    slugs: set[str] = set()
    for loc in locs:
        slug = _candidate_model_slug(loc)
        if slug:
            slugs.add(slug)
    return sorted(slugs)


async def find_new_models(ctx) -> list[str]:
    """Slugs that have a docs page but no registered ModelSpec yet, and
    were not already explicitly reviewed and declined (EXCLUDED_SLUGS)."""
    candidates = await fetch_candidate_slugs(ctx)
    known = _known_create_slugs()
    return [s for s in candidates if s not in known and s not in EXCLUDED_SLUGS]


# --------------------------- schedule state ---------------------------

async def _read_state(ctx) -> dict:
    try:
        doc = await ctx.store.get(DISCOVERY_STATE_COLLECTION, DISCOVERY_STATE_KEY)
    except Exception:
        doc = None
    data = {"last_date": "", "last_result": "", "last_found": []}
    if doc is not None:
        raw = getattr(doc, "data", None) or {}
        if isinstance(raw, dict):
            data.update({k: v for k, v in raw.items() if k in data})
    return data


async def _write_state(ctx, data: dict) -> None:
    try:
        await ctx.store.update(DISCOVERY_STATE_COLLECTION, DISCOVERY_STATE_KEY, data)
    except Exception:
        try:
            await ctx.store.create(DISCOVERY_STATE_COLLECTION,
                                    {"id": DISCOVERY_STATE_KEY, **data})
        except Exception:
            pass


async def due(ctx, *, ts: float | None = None) -> bool:
    """Same discipline as SEO Audit Engine's schedule_settings.due(): a
    date-based guard, not an elapsed-time one, so a slow check today can
    never cause two checks tomorrow."""
    state = await _read_state(ctx)
    now = time.gmtime(ts if ts is not None else time.time())
    if now.tm_hour < CHECK_HOUR_UTC:
        return False
    return str(state.get("last_date") or "") != _now_date(ts)


async def record_check(ctx, *, found: list[str], result: str,
                        ts: float | None = None) -> dict:
    """Permanently log this check -- one row per day, never overwritten --
    plus update the lightweight 'last checked' state used by due().

    Two collections on purpose: DISCOVERY_STATE_COLLECTION is a single
    mutable row (cheap due() reads); DISCOVERY_LOG_COLLECTION is an
    append-only history so 'was nano-banana-2 already flagged three weeks
    ago' stays answerable long after the state row has moved on -- exactly
    the same split SEO Audit Engine uses between its schedule settings and
    its actual run history.
    """
    date = _now_date(ts)
    entry = {
        "date": date,
        "checked_at": ts if ts is not None else time.time(),
        "found": list(found),
        "result": result,
    }
    try:
        await ctx.store.create(DISCOVERY_LOG_COLLECTION, entry)
    except Exception:
        pass
    await _write_state(ctx, {
        "last_date": date, "last_result": result, "last_found": list(found),
    })
    return entry


async def list_log(ctx, limit: int = 30) -> list[dict]:
    """Recent discovery-check history, newest first."""
    try:
        page = await ctx.store.query(DISCOVERY_LOG_COLLECTION, limit=200)
    except Exception:
        return []
    rows = [dict(d.data) for d in page.data]
    rows.sort(key=lambda r: r.get("checked_at", 0), reverse=True)
    return rows[:limit]

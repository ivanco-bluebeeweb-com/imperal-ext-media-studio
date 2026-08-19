"""The Image Prompt engine -- generation, analysis, deterministic fixes, and
a monthly self-review that WATCHES for drift without ever silently rewriting
its own rules.

WHY THIS EXISTS, SEPARATE FROM `shared.prompt_for_role`.

`shared.prompt_for_role` builds the base prompt (subject/summary + role
framing + style + text policy + language clause) -- that part stays exactly
as-is, unchanged, still the single source of truth for brief-time prompt
construction. What it never covered, confirmed against a cross-vendor study
of Google's own Gemini image guide, Black Forest Labs' Flux.2 guide, OpenAI's
GPT Image cookbook and BytePlus' Seedream guide (2026-08-14 research,
also saved as an Imperal note): most "flat"/generic-looking AI images trace
back to two missing prompt slots that a model then silently defaults on its
own -- LIGHTING and CAMERA/LENS language. This module is the seam that:

  1. GENERATES an enriched prompt (`generate_prompt`) -- calls
     `shared.prompt_for_role` unchanged, then deterministically appends a
     generic lighting/camera clause ONLY IF the constructed prompt doesn't
     already mention one (so a caller's own style_direction that already
     says "golden hour lighting, shot on 85mm" is never double-stamped).
  2. ANALYZES any prompt (`analyze_prompt`) against a 6-slot rubric (Subject,
     Environment, Composition, Lighting, Style/Medium, Technical/Camera) --
     a plain keyword-presence heuristic, not a semantic model. This is
     deliberate and stated honestly: a keyword hit proves a slot was
     ADDRESSED, not that it's good. Never claim more than that.
  3. FIXES a prompt (`fix_prompt`) -- runs the same analysis and appends
     the SAME generic lighting/camera/style clauses `generate_prompt` would
     have appended, for a prompt built outside this engine (e.g. a manual
     `prompt_override` on regenerate_asset). It NEVER invents subject or
     environment content -- those slots, if missing, are reported as
     `unfixable_issues` for a human to add real words to, exactly the same
     "never guess the actual content" discipline `model_discovery.py` uses
     for a new model's request-body shape.
  4. Runs a MONTHLY self-review (`run_self_review`) with the exact same
     shape as `model_discovery.py`'s daily check: hash-diff three vendors'
     own public prompting-guide pages (proof text actually changed, not a
     guess) and re-score a sample of this app's OWN already-generated
     prompts against the rubric. Every run is permanently logged, whether
     or not anything changed. If a guide's hash changed, or the sampled
     average score drops below a threshold, that is recorded as a finding
     for a human to review and, if warranted, manually update
     `_GENERIC_LIGHTING`/`_GENERIC_CAMERA`/`SLOT_KEYWORDS` below -- the same
     discipline as model_discovery.py's `EXCLUDED_SLUGS`/ModelSpec review:
     a docs page changing proves nothing about what changed IN it, so this
     never rewrites its own clause library automatically.
"""

from __future__ import annotations

import hashlib
import re
import time

import shared

#: Public, unauthenticated vendor prompting-guide pages, confirmed reachable
#: 2026-08-14 during the research this module is built from. Google's own
#: Gemini image-prompt guide is deliberately NOT in this list -- it is only
#: exposed through the Gemini connector's own tool, not a plain HTTPS page
#: this app could fetch honestly with ctx.http, and inventing a doc URL for
#: it would violate the "never guess a URL" discipline model_discovery.py
#: already established for Magnific's own docs.
GUIDE_SOURCES: tuple[tuple[str, str], ...] = (
    ("flux2_bfl", "https://docs.bfl.ml/guides/prompting_guide_flux2"),
    ("gpt_image_openai",
     "https://developers.openai.com/cookbook/examples/multimodal/"
     "image-gen-models-prompting-guide"),
    ("seedream_byteplus", "https://docs.byteplus.com/en/docs/ModelArk/1829186"),
)

#: Wakes hourly, asks "already ran this calendar month?" -- same shape as
#: model_discovery.py's TICK_CRON, a different minute so the two schedules
#: (both registered on this same extension) never fire in the same tick.
TICK_CRON = "20 * * * *"
CHECK_HOUR_UTC = 7

REVIEW_STATE_COLLECTION = "prompt_engine_state"
REVIEW_STATE_KEY = "state"
REVIEW_LOG_COLLECTION = "prompt_engine_log"

#: Below this average rubric score (0-100) across the sampled prompts, a
#: self-review run flags "review recommended" -- a signal for a human, never
#: a trigger for this module to rewrite itself.
SCORE_ALERT_THRESHOLD = 70

# --------------------------- the 6-slot rubric ---------------------------
#
# Keyword-presence heuristic ONLY. A hit means the slot was addressed in
# SOME form; it does not grade quality, originality, or whether the words
# make sense together. Every score/finding this module produces must be
# read with that honest ceiling in mind -- never oversold as "the prompt is
# good", only "the prompt mentions X".

SLOT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "subject": (),  # handled specially: non-empty prompt text itself
    "environment": (
        "in a", "at a", "on a", "inside", "outside", "background", "setting",
        "scene", "room", "street", "kitchen", "office", "studio backdrop",
        "landscape", "indoor", "outdoor", "against a",
    ),
    "composition": (
        "shot", "close-up", "close up", "wide", "hero", "detail", "angle",
        "framing", "aspect ratio", "top-down", "low-angle", "eye-level",
        "macro", "portrait", "landscape orientation", "centered",
    ),
    "lighting": (
        "lighting", "lit", "backlit", "golden hour", "sunlight", "daylight",
        "studio light", "ambient light", "soft light", "glow", "shadow",
        "exposure", "natural light", "warm light", "cool light",
    ),
    "style": (
        "photorealistic", "illustration", "cinematic", "watercolor",
        "3d render", "editorial", "studio photography", "vintage",
        "professional photo", "hyperrealistic", "digital art", "realism",
        "documentary style", "product photography",
    ),
    "technical": (
        "lens", "mm lens", "camera", "35mm", "50mm", "85mm", "telephoto",
        "wide-angle lens", "depth of field", "bokeh", "drone", "aerial",
        "film grain", "iso ", "aperture",
    ),
}

#: The two structural slots this engine will ever auto-append to -- always
#: generic, role-shaped boilerplate, never a guess at the subject itself.
#: Kept short and deliberately unopinionated; a real style_direction always
#: overrides these by already mentioning lighting/camera language (checked
#: via SLOT_KEYWORDS before appending, so this never double-stamps).
_GENERIC_LIGHTING = (
    "Lit with soft, natural directional lighting for realistic depth and shadow."
)
_GENERIC_CAMERA = {
    "featured": (
        "Captured as if shot on a 35mm lens at eye level with a shallow "
        "depth of field, professional editorial look."
    ),
    "inline": (
        "Captured as if shot on a 50mm lens with a tighter, detail-focused framing."
    ),
}
_GENERIC_STYLE = "professional editorial photography style"

#: SYSTEM-LEVEL GUARANTEE (added 2026-08-18, per direct ask: "даже если
#: отсутствует контекст в пайплайне -- проблем с этим не должно быть
#: никогда"): Environment/Context used to be in `fix_prompt`'s `unfixable`
#: bucket -- correct when the ONLY alternative was inventing a fake scene,
#: but that left every caller with no context at all (a bare title, no
#: summary, no visual_environment) producing a permanently-incomplete
#: prompt. This clause is honestly generic (never claims a specific scene
#: this engine has no evidence for) -- same discipline as generic lighting/
#: camera/style above: it fills the STRUCTURAL slot with a safe, neutral
#: default rather than leaving it empty, and a real `visual_environment`
#: from the caller (see shared.prompt_for_role) always takes priority over
#: this fallback.
_GENERIC_ENVIRONMENT = (
    "Set in a clean, realistic, well-lit professional environment consistent "
    "with the subject matter."
)


#: Persisted, user-editable overrides for the four generic clauses above
#: plus the review alert threshold -- one doc, defaults to the module
#: constants so an unconfigured install behaves exactly as before.
PROMPT_CONFIG_COLLECTION = "prompt_engine_config"
PROMPT_CONFIG_KEY = "config"

DEFAULT_PROMPT_CONFIG: dict = {
    "generic_lighting": _GENERIC_LIGHTING,
    "generic_camera_featured": _GENERIC_CAMERA["featured"],
    "generic_camera_inline": _GENERIC_CAMERA["inline"],
    "generic_style": _GENERIC_STYLE,
    "generic_environment": _GENERIC_ENVIRONMENT,
    "score_alert_threshold": SCORE_ALERT_THRESHOLD,
    # Standing directive (2026-08-18): generated images must never carry
    # legible text UNLESS a human explicitly turns this off where it's
    # appropriate (App settings > Image Prompt engine). Default True = the
    # system-wide ban is ON out of the box. See `generate_prompt` below for
    # WHERE this is enforced -- deliberately the single funnel every brief
    # prompt passes through, so the ban holds even for a caller/pipeline
    # stage that has no visual context at all to reason about text with.
    "forbid_image_text": True,
}

#: Config keys that are booleans, not free text -- `save_prompt_config`
#: needs to treat these differently from a blank-means-keep text field
#: (an explicit `False` must be saveable, not treated as "blank").
_BOOL_CONFIG_KEYS = frozenset({"forbid_image_text"})


async def get_prompt_config(ctx) -> dict:
    """The effective config: built-in defaults, overridden by whatever the
    user has saved via the App settings > Image Prompt engine tab. Missing
    or blank saved fields fall back to the default instead of surfacing an
    empty clause -- same discipline as `_read_state`/model_discovery.py."""
    try:
        doc = await ctx.store.get(PROMPT_CONFIG_COLLECTION, PROMPT_CONFIG_KEY)
    except Exception:
        doc = None
    config = dict(DEFAULT_PROMPT_CONFIG)
    if doc is not None:
        raw = getattr(doc, "data", None) or {}
        if isinstance(raw, dict):
            for key in config:
                value = raw.get(key)
                if key in _BOOL_CONFIG_KEYS:
                    if value is not None:
                        config[key] = bool(value)
                elif value not in (None, ""):
                    config[key] = value
    return config


async def save_prompt_config(ctx, updates: dict) -> dict:
    """Merge `updates` onto the current saved config and persist it.

    A blank text field is IGNORED, not saved as empty -- pasting an empty
    textarea then clicking Apply Changes must fall back to the built-in
    default on next read, never blank out the clause entirely.
    `score_alert_threshold` is clamped to 0-100 and any non-numeric value
    is ignored the same way. Boolean keys (see `_BOOL_CONFIG_KEYS`) always
    save whatever explicit True/False was given -- unlike a text field,
    `False` is a real, meaningful, savable value, never "blank".
    """
    config = await get_prompt_config(ctx)
    for key, value in updates.items():
        if key not in config:
            continue
        if key == "score_alert_threshold":
            try:
                value = max(0, min(100, int(value)))
            except (TypeError, ValueError):
                continue
        elif key in _BOOL_CONFIG_KEYS:
            value = bool(value)
        else:
            value = str(value).strip()
            if not value:
                continue
        config[key] = value
    try:
        await ctx.store.update(PROMPT_CONFIG_COLLECTION, PROMPT_CONFIG_KEY, config)
    except Exception:
        try:
            await ctx.store.create(PROMPT_CONFIG_COLLECTION,
                                    {"id": PROMPT_CONFIG_KEY, **config})
        except Exception:
            pass
    return config


def _role_bucket(role: str) -> str:
    return "featured" if role == "featured" else "inline"


def _has_any(prompt_lower: str, keywords: tuple[str, ...]) -> bool:
    return any(kw in prompt_lower for kw in keywords)


def analyze_prompt(prompt: str) -> dict:
    """Score `prompt` against the 6-slot rubric.

    Returns a dict with `score` (0-100, one of 6 slots per point since
    "subject" is trivially satisfied by any non-empty prompt), `covered`
    and `missing` slot-name lists, so a caller can tell exactly which of
    the 6 the heuristic did NOT find a keyword for.
    """
    text = (prompt or "").strip()
    if not text:
        return {"score": 0, "covered": [], "missing": list(SLOT_KEYWORDS.keys())}
    lower = text.lower()
    covered: list[str] = []
    missing: list[str] = []
    for slot, keywords in SLOT_KEYWORDS.items():
        if slot == "subject":
            covered.append(slot)  # non-empty prompt text IS the subject slot
            continue
        if _has_any(lower, keywords):
            covered.append(slot)
        else:
            missing.append(slot)
    score = round(100 * len(covered) / len(SLOT_KEYWORDS))
    return {"score": score, "covered": covered, "missing": missing}


def fix_prompt(
    prompt: str, role: str = "featured", config: dict | None = None,
) -> tuple[str, list[str], list[str]]:
    """Deterministically fix the structural slots this engine can fill
    without guessing real content (lighting, camera, generic style, and
    -- since 2026-08-18 -- generic environment) fallback). Returns
    (fixed_prompt, additions, unfixable).

    `additions` lists what was appended, in plain English, so a caller can
    show exactly what changed -- never a silent rewrite. `unfixable`
    carries any of {"subject", "composition"} still missing after the fix:
    those genuinely need real words from the caller (article_title/summary/
    visual_subject), which this engine will never invent. `environment` is
    NO LONGER unfixable -- see `_GENERIC_ENVIRONMENT`'s docstring: a bare,
    honestly-generic fallback is filled in rather than ever leaving the
    slot empty, exactly like lighting/camera/style already did.

    `config`, if given, is an effective config dict shaped like
    `DEFAULT_PROMPT_CONFIG` (as returned by `get_prompt_config`) -- lets a
    caller apply the user's saved Image Prompt engine settings instead of
    the hardcoded module defaults. Omitted/None keeps the exact original
    behaviour (module constants), so every existing synchronous caller and
    test needs no change.
    """
    cfg = config or DEFAULT_PROMPT_CONFIG
    generic_lighting = cfg.get("generic_lighting") or _GENERIC_LIGHTING
    generic_camera = {
        "featured": cfg.get("generic_camera_featured") or _GENERIC_CAMERA["featured"],
        "inline": cfg.get("generic_camera_inline") or _GENERIC_CAMERA["inline"],
    }
    generic_style = cfg.get("generic_style") or _GENERIC_STYLE
    generic_environment = cfg.get("generic_environment") or _GENERIC_ENVIRONMENT

    text = (prompt or "").strip()
    analysis = analyze_prompt(text)
    missing = set(analysis["missing"])
    additions: list[str] = []
    fixed = text

    if "lighting" in missing:
        fixed = f"{fixed} {generic_lighting}".strip()
        additions.append(f"Added lighting clause: \"{generic_lighting}\"")
        missing.discard("lighting")

    if "technical" in missing:
        camera_clause = generic_camera[_role_bucket(role)]
        fixed = f"{fixed} {camera_clause}".strip()
        additions.append(f"Added camera/lens clause: \"{camera_clause}\"")
        missing.discard("technical")

    if "style" in missing:
        fixed = f"{fixed} Style: {generic_style}.".strip()
        additions.append(f"Added generic style fallback: \"{generic_style}\"")
        missing.discard("style")

    if "environment" in missing:
        fixed = f"{fixed} {generic_environment}".strip()
        additions.append(f"Added generic environment fallback: \"{generic_environment}\"")
        missing.discard("environment")

    unfixable = sorted(missing & {"subject", "composition"})
    return fixed, additions, unfixable


def generate_prompt(
    role: str, article_title: str, summary: str, style_direction: str,
    lang: str = "", text_policy: str = shared.TEXT_POLICY_NO_TEXT,
    image_text: str = "", config: dict | None = None,
    visual_subject: str = "", visual_environment: str = "",
) -> str:
    """The engine's own entry point for BRIEF-TIME prompt generation --
    same inputs/output shape as `shared.prompt_for_role`, but auto-enriched
    with a lighting/camera/environment clause when the base construction
    doesn't already carry one (e.g. no style_direction was given, or it
    didn't mention either). This is the function `create_media_brief`
    calls; the underlying `shared.prompt_for_role` keeps working exactly as
    before for any other caller/test that still calls it directly.

    `visual_subject`/`visual_environment`: pass through to
    `shared.prompt_for_role` -- see its docstring. Giving real values here
    (e.g. from Content Strategy Hub's approved_visual_guidance) is always
    preferred; when omitted, `fix_prompt` below still guarantees Environment
    is never left blank via `_GENERIC_ENVIRONMENT`. This is the two-layer
    guarantee: real context wins when available, an honest generic
    fallback covers the case where the caller had none at all -- Subject
    is the one slot this engine will never fabricate, by design.

    `config`: see `fix_prompt` -- pass the user's saved settings (fetched
    once via `await get_prompt_config(ctx)`) so brief-time generation
    reflects whatever was last saved from App settings.

    TEXT BAN ENFORCEMENT (standing directive, 2026-08-18): when
    `config["forbid_image_text"]` is true (the default), `text_policy` is
    forced to `shared.TEXT_POLICY_NO_TEXT` here, UNCONDITIONALLY, no matter
    what a caller asked for -- this is the one funnel every brief-time
    prompt passes through, so the ban holds system-wide even for a pipeline
    stage that has no visual-brand context at all to reason with. A missing
    `config` (a legacy/test caller that never fetched one) defaults to
    banned too, via `effective_config` below -- there is no code path where
    "no config" quietly means "text allowed". Only an explicit, human-saved
    `forbid_image_text: False` in App settings lets `text_policy`/
    `image_text` through as given.
    """
    effective_config = config if config is not None else DEFAULT_PROMPT_CONFIG
    if effective_config.get("forbid_image_text", True):
        text_policy = shared.TEXT_POLICY_NO_TEXT
        image_text = ""
    base = shared.prompt_for_role(
        role, article_title, summary, style_direction, lang, text_policy, image_text,
        visual_subject, visual_environment,
    )
    fixed, _additions, _unfixable = fix_prompt(base, role, config)
    return fixed


# --------------------------- monthly self-review ---------------------------

def _now_month(ts: float | None = None) -> str:
    return time.strftime("%Y-%m", time.gmtime(ts if ts is not None else time.time()))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


async def _fetch_guide_hash(ctx, url: str) -> tuple[bool, str]:
    """(reachable, sha256_of_body). Never raises -- an unreachable source is
    a normal, expected outcome (a vendor page moves/blocks bots), reported
    per-source rather than failing the whole review run."""
    try:
        resp = await ctx.http.get(url, timeout=30)
        if not (200 <= resp.status_code < 300):
            return False, ""
        body = resp.text()
        return True, _sha256(body)
    except Exception:
        return False, ""


async def _read_state(ctx) -> dict:
    try:
        doc = await ctx.store.get(REVIEW_STATE_COLLECTION, REVIEW_STATE_KEY)
    except Exception:
        doc = None
    data = {"last_month": "", "guide_hashes": {}}
    if doc is not None:
        raw = getattr(doc, "data", None) or {}
        if isinstance(raw, dict):
            data.update({k: v for k, v in raw.items() if k in data})
    return data


async def _write_state(ctx, data: dict) -> None:
    try:
        await ctx.store.update(REVIEW_STATE_COLLECTION, REVIEW_STATE_KEY, data)
    except Exception:
        try:
            await ctx.store.create(REVIEW_STATE_COLLECTION,
                                    {"id": REVIEW_STATE_KEY, **data})
        except Exception:
            pass


async def due(ctx, *, ts: float | None = None) -> bool:
    """True once per calendar month, after CHECK_HOUR_UTC -- same date-guard
    discipline as model_discovery.due(), just compared by month instead of
    by day, and self-catching-up: if the app was paused/offline through the
    1st, the next hourly tick after CHECK_HOUR_UTC on ANY later day this
    month still fires it exactly once (last_month simply won't match yet)."""
    state = await _read_state(ctx)
    now = time.gmtime(ts if ts is not None else time.time())
    if now.tm_hour < CHECK_HOUR_UTC:
        return False
    return str(state.get("last_month") or "") != _now_month(ts)


async def _sample_recent_prompts(ctx, limit: int = 20) -> list[str]:
    """Grade the engine's own recent real output, not synthetic examples --
    pulls prompts straight off already-generated media package assets."""
    import storage as st
    packages = await st.list_packages(ctx, limit=50)
    prompts: list[str] = []
    for pkg in packages:
        for asset in pkg.get("assets", []):
            p = (asset.get("prompt") or "").strip()
            if p:
                prompts.append(p)
            if len(prompts) >= limit:
                return prompts
    return prompts


async def run_self_review(ctx, *, ts: float | None = None) -> dict:
    """The actual monthly job: hash-diff the 3 vendor guide pages against
    last month's stored hashes, and re-score a sample of this app's own
    recent prompts. Always returns a full result and always gets logged by
    the caller -- this function itself never writes the permanent log, so
    the manual `check_prompt_engine_updates` tool and the scheduled tick
    share EXACTLY one code path with no drift between them.

    The alert threshold is the user's saved `score_alert_threshold` (App
    settings > Image Prompt engine), falling back to the module default --
    fetched fresh here so a threshold change takes effect on the very next
    run without needing a redeploy."""
    config = await get_prompt_config(ctx)
    alert_threshold = config.get("score_alert_threshold", SCORE_ALERT_THRESHOLD)
    state = await _read_state(ctx)
    old_hashes: dict = dict(state.get("guide_hashes") or {})
    new_hashes: dict[str, str] = {}
    guides_changed: list[str] = []
    guides_unreachable: list[str] = []

    for name, url in GUIDE_SOURCES:
        reachable, digest = await _fetch_guide_hash(ctx, url)
        if not reachable:
            guides_unreachable.append(name)
            if name in old_hashes:
                new_hashes[name] = old_hashes[name]  # keep last-known, don't wipe on a blip
            continue
        new_hashes[name] = digest
        if name in old_hashes and old_hashes[name] != digest:
            guides_changed.append(name)

    sample = await _sample_recent_prompts(ctx)
    scores = [analyze_prompt(p)["score"] for p in sample]
    avg_score = round(sum(scores) / len(scores)) if scores else None

    review_recommended = bool(guides_changed) or (
        avg_score is not None and avg_score < alert_threshold
    )

    notes = []
    if guides_changed:
        notes.append(
            f"{len(guides_changed)} guide(s) changed since last check: "
            f"{', '.join(guides_changed)} -- read the page and decide by hand "
            "whether SLOT_KEYWORDS/the generic clauses in App settings > "
            "Image Prompt engine need updating. Never auto-applied."
        )
    if guides_unreachable:
        notes.append(f"Could not reach: {', '.join(guides_unreachable)} (skipped, kept last-known hash).")
    if avg_score is not None:
        notes.append(
            f"Sampled {len(sample)} recent generated prompt(s), average rubric "
            f"score {avg_score}/100."
            + (" Below alert threshold -- review recommended." if avg_score < alert_threshold else "")
        )
    else:
        notes.append("No generated prompts found yet to sample.")

    await _write_state(ctx, {
        "last_month": _now_month(ts),
        "guide_hashes": new_hashes,
    })

    return {
        "checked_at": ts if ts is not None else time.time(),
        "guides_checked": len(GUIDE_SOURCES),
        "guides_changed": guides_changed,
        "guides_unreachable": guides_unreachable,
        "sample_size": len(sample),
        "avg_score": avg_score,
        "review_recommended": review_recommended,
        "note": " ".join(notes),
    }


async def record_review(ctx, result: dict) -> dict:
    """Permanently log this review -- one row per run, never overwritten,
    same append-only discipline as model_discovery.record_check."""
    entry = {
        "checked_at": result["checked_at"],
        "guides_changed": list(result["guides_changed"]),
        "guides_unreachable": list(result["guides_unreachable"]),
        "sample_size": result["sample_size"],
        "avg_score": result["avg_score"],
        "review_recommended": result["review_recommended"],
        "note": result["note"],
    }
    try:
        await ctx.store.create(REVIEW_LOG_COLLECTION, entry)
    except Exception:
        pass
    return entry


async def list_review_log(ctx, limit: int = 30) -> list[dict]:
    try:
        page = await ctx.store.query(REVIEW_LOG_COLLECTION, limit=200)
    except Exception:
        return []
    rows = [dict(d.data) for d in page.data]
    rows.sort(key=lambda r: r.get("checked_at", 0), reverse=True)
    return rows[:limit]

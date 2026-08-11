"""Multi-provider model registry -- the seam that lets Media Hub call
Google/OpenAI-backed models through Magnific, not just Magnific's own
Mystic, plus an honest "auto" picker mirroring the web app's Auto mode.

WHY EACH MODEL IS A SEPARATE ENDPOINT, NOT ONE ENDPOINT WITH A `model=` ENUM.

Confirmed directly against docs.magnific.com/api-reference: Mystic, Imagen 4
(Fast/Ultra), Gemini 2.5 Flash / Nano Banana Pro Flash, Seedream 4/4.5,
Flux Pro/Dev/2, etc. are each their OWN REST path with their OWN request
body shape (Imagen4 wants `aspect_ratio`/`person_generation`/
`safety_settings`; Gemini/Nano Banana want a `reference_images` array;
Mystic wants `num_images` + its own style enum). So "add other models" is a
small per-model adapter, not a single new parameter -- this file is that
adapter table. One row = {endpoint path, body builder, task-status path}.
Response *parsing* (extract task id / status / urls) is unchanged across
rows -- Magnific's task lifecycle vocabulary (pending/completed/failed) and
wrapping convention (`{"data": {...}}`) is the same for every async task
endpoint they document, so `magnific_client._extract_*` keeps being reused
verbatim; only request-building differs per model.

WHY THIS FILE DOES NOT INCLUDE GPT IMAGE.

The user's brief mentioned OpenAI models by name (they show up in
Magnific's web app and MCP model list). I looked for a documented
docs.magnific.com/api-reference page for a GPT Image endpoint and could not
confirm one -- every guessed URL pattern that matches the other models'
convention (.../gpt-image-1-5/overview, .../post-gpt-image-1-5, etc.) 404s.
Rather than invent a path and ship a request that would fail at
runtime with a confusing error, GPT Image is left OUT of `MODELS` until a
real endpoint is confirmed. Everything else below (Imagen 4 Fast/Ultra,
Gemini 2.5 Flash / Nano Banana Pro Flash) IS individually confirmed against
Magnific's own docs pages, including the exact path and field names.

WHY 2026-08-11's EXPANSION (Nano Banana Pro, Nano Banana Pro Flash, Flux
Dev/Pro 1.1/2 Pro/2 Turbo, HyperFlux, Z-Image, Seedream 4/4.5/V5 Lite/V5 Pro)
AND WHY "NANO BANANA 2" SPECIFICALLY ISN'T A ROW.

The user could pick "Nano Banana 2" in Magnific's own WEB APP but every
guessed REST path for it 404s, and the full docs.magnific.com sitemap
(fetched directly, not guessed) confirms no page for it exists -- only
`nano-banana-pro` and `nano-banana-pro-flash`. Three explanations are all
plausible (the web app ships ahead of the documented REST API; "Nano Banana
2" is a marketing label for what the API calls nano-banana-pro under the
hood; or the docs are stale) and none can be confirmed without the exact
endpoint appearing in the docs. So exactly like GPT Image above, "Nano
Banana 2" itself is deliberately NOT a row -- only the two Nano Banana
variants with a confirmed, sitemap-listed docs page are added.

Imagen 3 (`docs.magnific.com/api-reference/text-to-image/post-imagen3`) is
NOT added even though it has a confirmed page: that page's own text says
"This endpoint is deprecated and will be removed on 2026/05/13" -- a date
already in the past relative to today. Adding a model Magnific itself says
is already gone would just hand back a confusing failure later.

Every row added below was confirmed the same way the original four were:
a real `curl`/path block AND a real request-body field list read directly
off that model's own docs.magnific.com page -- not inferred from another
model's shape. Three genuinely different `aspect_ratio` vocabularies exist
across these pages (the `square_1_1`/`classic_4_3`/... family used by
Mystic/Imagen4/Flux-Dev/Flux-Pro-1.1/HyperFlux/Seedream-4; the wider
`square_1_1`/.../`cinematic_21_9` family used by Seedream 4.5/V5
Lite/V5 Pro; and Nano Banana Pro/Flash's own colon-style `\"4:3\"` family) --
each body-builder below uses ONLY the enum confirmed on ITS OWN page, never
borrowed from a sibling. Flux 2 Pro/Turbo and Z-Image don't use
`aspect_ratio` at all -- Flux 2 takes raw pixel `width`/`height`, Z-Image
takes a named `image_size` enum -- so their builders set the closest
documented 4:3-shaped default instead of a nonexistent field.

WHY THERE IS AN "AUTO" MODE, AND WHY IT IS HONEST ABOUT WHAT IT DOES.

The web app's Magnific Auto mode is opaque by Magnific's own admission (see
their MCP FAQ: "The server automatically chooses the best model based on
the context ... there's no way to know for sure without checking the
resulting creation afterward"). We cannot reproduce Magnific's internal
choice -- there is no documented endpoint that exposes it. So `pick_model`
below is NOT a claim to replicate Magnific's black box; it is Media Hub's
OWN transparent heuristic that mirrors the same *goal* (stop defaulting
blindly to one model, match the model to the job) using signals we already
have: role (featured wants max fidelity; inline wants speed/cost), and
simple keyword cues in the prompt/style_direction (photorealistic vs.
illustrative vs. portrait-heavy). It always returns a real row from
`MODELS` and the picked id is stored on the asset, so it is fully visible
and overridable -- never a silent, unexplainable choice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class ModelSpec:
    """One callable model: how to build its Magnific request and where.

    `response_kind` exists because "Classic fast" (added 2026-08-11,
    confirmed via docs.magnific.com/api-reference/text-to-image/get-image-from-text)
    is genuinely a different shape from every other model here: it is a
    SINGLE synchronous POST /v1/ai/text-to-image call that returns
    `{"data": [{"base64": "..."}]}` directly -- no task id, no polling,
    no hosted URL at all. Every other model is "async_url": create a task,
    poll status_path, get back a ready-made image URL. Treating Classic
    fast as an "async_url" model with an empty status_path would be a
    silent lie about its actual contract, so it gets its own explicit
    tag instead of being forced into the polling shape.
    """
    id: str
    provider: str                 # "magnific" | "google" | ... (who trained it)
    label: str                    # human-readable, for UI/errors
    create_path: str              # POST path, relative to BASE_URL
    status_path: str              # GET path template, {task_id} placeholder -- "" for sync_base64
    build_body: Callable[[str], dict]  # prompt -> request body
    tags: tuple[str, ...] = field(default_factory=tuple)  # heuristic hints
    response_kind: str = "async_url"  # "async_url" | "sync_base64"


# Every blogpost image this pipeline generates must be 4:3 landscape (the
# user's explicit standing directive). `classic_4_3` is the exact enum value
# confirmed on BOTH Mystic's and Imagen 4's documented request bodies
# (docs.magnific.com/api-reference/mystic/post-mystic lists `aspect_ratio`
# with a `square_1_1` default and the same enum set as Imagen4's
# .../text-to-image/imagen4-fast/generate page -- both include `classic_4_3`
# = "4:3, horizontal/landscape"). Kept as one constant here (mirrored in
# shared.ASPECT_RATIO_4_3) so both body-builders below use the identical
# value -- no per-model drift.
ASPECT_RATIO_4_3 = "classic_4_3"


def _mystic_body(prompt: str) -> dict:
    # `aspect_ratio` confirmed on Mystic's own docs page (default square_1_1,
    # same enum as Imagen4) -- omitted historically only because v1 never
    # asked for anything but the square default; every blogpost image now
    # must be 4:3 landscape, so it's set explicitly.
    return {"prompt": prompt, "num_images": 1, "aspect_ratio": ASPECT_RATIO_4_3}


def _imagen4_body(prompt: str) -> dict:
    # Confirmed fields: docs.magnific.com/api-reference/text-to-image/imagen4-fast/generate
    # Was hardcoded to widescreen_16_9 -- corrected to the pipeline-wide 4:3
    # landscape requirement.
    return {"prompt": prompt, "aspect_ratio": ASPECT_RATIO_4_3}


def _gemini_flash_body(prompt: str) -> dict:
    # Confirmed fields: docs.magnific.com/api-reference/text-to-image/post-gemini-2-5-flash-image-preview
    # NOTE: Gemini 2.5 Flash has NO aspect_ratio field in Magnific's docs --
    # confirmed by reading the actual field list, not inferred. Adding one
    # here would be a fabricated/unsupported parameter, so this model is a
    # documented EXCEPTION to the pipeline's 4:3 rule until Magnific adds
    # that control for it.
    return {"prompt": prompt}


# Nano Banana Pro / Pro Flash use their OWN colon-style aspect_ratio enum
# ("1:1", "4:3", ...), confirmed on their own docs pages -- NOT the
# square_1_1-style enum used by Mystic/Imagen4/Flux/Seedream. Using the
# wrong family's string here would silently be an invalid value, so this is
# its own constant rather than reusing ASPECT_RATIO_4_3.
_NANO_BANANA_ASPECT_4_3 = "4:3"


def _nano_banana_body(prompt: str) -> dict:
    # Confirmed fields: docs.magnific.com/api-reference/text-to-image/post-nano-banana-pro
    # and .../nano-banana-pro-flash/generate -- both share the identical
    # prompt/aspect_ratio/resolution shape.
    return {"prompt": prompt, "aspect_ratio": _NANO_BANANA_ASPECT_4_3}


def _flux_dev_body(prompt: str) -> dict:
    # Confirmed fields: docs.magnific.com/api-reference/text-to-image/flux-dev/post-flux-dev
    # Uses the WIDER square_1_1-style enum (includes horizontal_2_1/
    # vertical_1_2/social_post_4_5 that Mystic/Imagen4 don't have) -- but
    # classic_4_3 is present in both, so the shared constant is still valid.
    return {"prompt": prompt, "aspect_ratio": ASPECT_RATIO_4_3}


def _flux_pro_v1_1_body(prompt: str) -> dict:
    # Confirmed fields: docs.magnific.com/api-reference/text-to-image/flux-pro-v1-1/post-flux-pro-v1-1
    # NOTE: this page's Body section lists NO `prompt` field at all in the
    # fetched content (only aspect_ratio + callback_url) -- an unusual gap
    # for a text-to-image endpoint. `prompt` is kept here because every
    # sibling Flux page documents one and Magnific's UI requires text input;
    # omitting the one field that actually drives generation would make this
    # model unusable. aspect_ratio uses the same wider enum as Flux Dev.
    return {"prompt": prompt, "aspect_ratio": ASPECT_RATIO_4_3}


def _hyperflux_body(prompt: str) -> dict:
    # Confirmed fields: docs.magnific.com/api-reference/text-to-image/post-hyperflux
    # Same square_1_1-style enum family as Flux Dev/Pro 1.1.
    return {"prompt": prompt, "aspect_ratio": ASPECT_RATIO_4_3}


def _flux2_pro_body(prompt: str) -> dict:
    # Confirmed fields: docs.magnific.com/api-reference/text-to-image/post-flux-2-pro
    # Flux 2 Pro has NO aspect_ratio enum at all -- only raw pixel width/
    # height (256-1440, default 1024x768). Closest to this pipeline's 4:3
    # landscape rule using its own documented example sizes (1024 width is
    # the default; 768 height keeps a 4:3-like ratio without inventing a
    # field that doesn't exist).
    return {"prompt": prompt, "width": 1024, "height": 768}


def _flux2_turbo_body(prompt: str) -> dict:
    # Confirmed fields: docs.magnific.com/api-reference/text-to-image/post-flux-2-turbo
    # Also pixel-based (custom width/height 512-2048, default 1024x1024) --
    # no aspect_ratio enum, so the closest 4:3-shaped explicit size is set.
    return {"prompt": prompt, "width": 1024, "height": 768}


def _zimage_body(prompt: str) -> dict:
    # Confirmed fields: docs.magnific.com/api-reference/text-to-image/post-z-image
    # Uses a named `image_size` enum (square/square_hd/portrait_3_4/
    # portrait_9_16/landscape_4_3/landscape_16_9) -- landscape_4_3 is an
    # EXACT documented match for this pipeline's 4:3 landscape rule.
    return {"prompt": prompt, "image_size": "landscape_4_3"}


def _seedream4_body(prompt: str) -> dict:
    # Confirmed fields: docs.magnific.com/api-reference/text-to-image/seedream-4/post-seedream-v4
    # NOTE: this page's Body section lists NO `prompt` field in the fetched
    # content either (only aspect_ratio + callback_url) -- same
    # documentation gap as Flux Pro 1.1. Kept for the same reason: every
    # sibling Seedream page documents `prompt` and it's what actually drives
    # generation. Same square_1_1-style enum family as Mystic/Imagen4.
    return {"prompt": prompt, "aspect_ratio": ASPECT_RATIO_4_3}


# Seedream 4.5 / V5 Lite / V5 Pro share the WIDER aspect_ratio enum
# (adds cinematic_21_9) confirmed identically on all three of their own
# docs pages -- classic_4_3 is present in this enum too, so the shared
# ASPECT_RATIO_4_3 constant is correct here as well.
def _seedream45_body(prompt: str) -> dict:
    # Confirmed fields: docs.magnific.com/api-reference/text-to-image/post-seedream-v4-5
    return {"prompt": prompt, "aspect_ratio": ASPECT_RATIO_4_3}


def _seedream5_lite_body(prompt: str) -> dict:
    # Confirmed fields: docs.magnific.com/api-reference/text-to-image/post-seedream-v5-lite
    return {"prompt": prompt, "aspect_ratio": ASPECT_RATIO_4_3}


def _seedream5_pro_body(prompt: str) -> dict:
    # Confirmed fields: docs.magnific.com/api-reference/text-to-image/post-seedream-v5-pro
    # Also takes a `resolution` tier (1.5k/2k) -- left unset to use the
    # documented default (2k) rather than guessing a preference.
    return {"prompt": prompt, "aspect_ratio": ASPECT_RATIO_4_3}


def _flux2_flex_body(prompt: str) -> dict:
    # Confirmed fields: docs.magnific.com/api-reference/text-to-image/flux-2-flex/generate
    # Pixel-based (width/height 256-1920, defaults 1024x768) -- no
    # aspect_ratio enum. The documented default IS already a 4:3-shaped
    # landscape (1024x768), so it is left at that default rather than
    # setting an explicit value that would just repeat it.
    return {"prompt": prompt, "width": 1024, "height": 768}


def _flux2_klein_body(prompt: str) -> dict:
    # Confirmed fields: docs.magnific.com/api-reference/text-to-image/flux-2-klein/generate
    # Uses its OWN square_1_1-style aspect_ratio enum (adds horizontal_2_1/
    # vertical_1_2/social_post_4_5 like Flux Dev) -- classic_4_3 present, so
    # the shared ASPECT_RATIO_4_3 constant applies here too.
    return {"prompt": prompt, "aspect_ratio": ASPECT_RATIO_4_3}


# RunWay text-to-image is its OWN THIRD aspect_ratio family -- confirmed on
# docs.magnific.com/api-reference/text-to-image/post-runway: raw
# "width:height" pixel strings (e.g. "1280:720"), not square_1_1-style enum
# names and not the colon-DIVISION style Nano Banana uses ("4:3" is a ratio;
# RunWay's "1280:720" is literal pixel dimensions -- they only coincide by
# accident for 1:1). 1280:720 is RunWay's own documented HD-landscape option.
_RUNWAY_ASPECT_LANDSCAPE = "1280:720"


def _runway_body(prompt: str) -> dict:
    return {"prompt": prompt, "aspect_ratio": _RUNWAY_ASPECT_LANDSCAPE}


# "Classic fast" is the ONE model on a genuinely different aspect_ratio
# vocabulary AND a genuinely different response contract (see ModelSpec's
# `response_kind` docstring) -- confirmed via
# docs.magnific.com/api-reference/text-to-image/get-image-from-text.md
# (the machine-readable OpenAPI-embedded doc, not the JS-rendered page).
# `classic_4_3` is present in this endpoint's own aspect_ratio enum too,
# so the shared landscape constant still applies.
def _classic_fast_body(prompt: str) -> dict:
    return {"prompt": prompt, "aspect_ratio": ASPECT_RATIO_4_3, "num_images": 1}


MODELS: dict[str, ModelSpec] = {
    "mystic": ModelSpec(
        id="mystic",
        provider="magnific",
        label="Magnific Mystic (default)",
        create_path="/v1/ai/mystic",
        status_path="/v1/ai/mystic/{task_id}",
        build_body=_mystic_body,
        tags=("general", "editorial", "fast", "featured", "inline"),
    ),
    "imagen4-fast": ModelSpec(
        id="imagen4-fast",
        provider="google",
        label="Google Imagen 4 Fast",
        create_path="/v1/ai/text-to-image/imagen4-fast",
        status_path="/v1/ai/text-to-image/imagen4-fast/{task_id}",
        build_body=_imagen4_body,
        tags=("photorealistic", "product", "ecommerce", "fast", "inline"),
    ),
    "imagen4-ultra": ModelSpec(
        id="imagen4-ultra",
        provider="google",
        label="Google Imagen 4 Ultra",
        create_path="/v1/ai/text-to-image/imagen4-ultra",
        status_path="/v1/ai/text-to-image/imagen4-ultra/{task_id}",
        build_body=_imagen4_body,
        tags=("photorealistic", "hero", "professional", "featured"),
    ),
    "gemini-2.5-flash": ModelSpec(
        id="gemini-2.5-flash",
        provider="google",
        label="Google Gemini 2.5 Flash",
        create_path="/v1/ai/gemini-2-5-flash-image-preview",
        status_path="/v1/ai/gemini-2-5-flash-image-preview/{task_id}",
        build_body=_gemini_flash_body,
        tags=("illustrative", "portrait", "people", "inline"),
    ),
    # -- 2026-08-11 expansion: every path/body below individually confirmed
    # against docs.magnific.com (see the module docstring's "WHY
    # 2026-08-11's EXPANSION" section for what was deliberately left out
    # and why).
    "nano-banana-pro": ModelSpec(
        id="nano-banana-pro",
        provider="google",
        label="Google Nano Banana Pro",
        create_path="/v1/ai/text-to-image/nano-banana-pro",
        status_path="/v1/ai/text-to-image/nano-banana-pro/{task_id}",
        build_body=_nano_banana_body,
        tags=("photorealistic", "hero", "professional", "featured", "portrait", "people"),
    ),
    "nano-banana-pro-flash": ModelSpec(
        id="nano-banana-pro-flash",
        provider="google",
        label="Google Nano Banana Pro Flash",
        create_path="/v1/ai/text-to-image/nano-banana-pro-flash",
        status_path="/v1/ai/text-to-image/nano-banana-pro-flash/{task_id}",
        build_body=_nano_banana_body,
        tags=("photorealistic", "fast", "inline", "portrait", "people"),
    ),
    "flux-dev": ModelSpec(
        id="flux-dev",
        provider="flux",
        label="Flux Dev",
        create_path="/v1/ai/text-to-image/flux-dev",
        status_path="/v1/ai/text-to-image/flux-dev/{task_id}",
        build_body=_flux_dev_body,
        tags=("illustrative", "general", "inline"),
    ),
    "flux-pro-v1.1": ModelSpec(
        id="flux-pro-v1.1",
        provider="flux",
        label="Flux Pro 1.1",
        create_path="/v1/ai/text-to-image/flux-pro-v1-1",
        status_path="/v1/ai/text-to-image/flux-pro-v1-1/{task_id}",
        build_body=_flux_pro_v1_1_body,
        tags=("photorealistic", "professional", "featured"),
    ),
    "flux-2-pro": ModelSpec(
        id="flux-2-pro",
        provider="flux",
        label="Flux 2 Pro",
        create_path="/v1/ai/text-to-image/flux-2-pro",
        status_path="/v1/ai/text-to-image/flux-2-pro/{task_id}",
        build_body=_flux2_pro_body,
        tags=("photorealistic", "professional", "featured"),
    ),
    "flux-2-turbo": ModelSpec(
        id="flux-2-turbo",
        provider="flux",
        label="Flux 2 Turbo",
        create_path="/v1/ai/text-to-image/flux-2-turbo",
        status_path="/v1/ai/text-to-image/flux-2-turbo/{task_id}",
        build_body=_flux2_turbo_body,
        tags=("fast", "inline", "general"),
    ),
    "hyperflux": ModelSpec(
        id="hyperflux",
        provider="flux",
        label="HyperFlux",
        create_path="/v1/ai/text-to-image/hyperflux",
        status_path="/v1/ai/text-to-image/hyperflux/{task_id}",
        build_body=_hyperflux_body,
        tags=("fast", "inline", "general"),
    ),
    "z-image": ModelSpec(
        id="z-image",
        provider="z-image",
        label="Z-Image",
        create_path="/v1/ai/text-to-image/z-image",
        status_path="/v1/ai/text-to-image/z-image/{task_id}",
        build_body=_zimage_body,
        tags=("fast", "inline", "general"),
    ),
    "flux-2-flex": ModelSpec(
        id="flux-2-flex",
        provider="flux",
        label="Flux 2 Flex",
        create_path="/v1/ai/text-to-image/flux-2-flex",
        status_path="/v1/ai/text-to-image/flux-2-flex/{task_id}",
        build_body=_flux2_flex_body,
        tags=("professional", "featured", "general"),
    ),
    "flux-2-klein": ModelSpec(
        id="flux-2-klein",
        provider="flux",
        label="Flux 2 Klein",
        create_path="/v1/ai/text-to-image/flux-2-klein",
        status_path="/v1/ai/text-to-image/flux-2-klein/{task_id}",
        build_body=_flux2_klein_body,
        tags=("fast", "inline", "general"),
    ),
    "seedream-4": ModelSpec(
        id="seedream-4",
        provider="seedream",
        label="Seedream 4",
        create_path="/v1/ai/text-to-image/seedream-v4",
        status_path="/v1/ai/text-to-image/seedream-v4/{task_id}",
        build_body=_seedream4_body,
        tags=("illustrative", "typography", "general", "inline"),
    ),
    "seedream-4.5": ModelSpec(
        id="seedream-4.5",
        provider="seedream",
        label="Seedream 4.5",
        create_path="/v1/ai/text-to-image/seedream-v4-5",
        status_path="/v1/ai/text-to-image/seedream-v4-5/{task_id}",
        build_body=_seedream45_body,
        tags=("illustrative", "typography", "featured"),
    ),
    "seedream-v5-lite": ModelSpec(
        id="seedream-v5-lite",
        provider="seedream",
        label="Seedream V5 Lite",
        create_path="/v1/ai/text-to-image/seedream-v5-lite",
        status_path="/v1/ai/text-to-image/seedream-v5-lite/{task_id}",
        build_body=_seedream5_lite_body,
        tags=("general", "inline", "fast"),
    ),
    "seedream-v5-pro": ModelSpec(
        id="seedream-v5-pro",
        provider="seedream",
        label="Seedream V5 Pro",
        create_path="/v1/ai/text-to-image/seedream-v5-pro",
        status_path="/v1/ai/text-to-image/seedream-v5-pro/{task_id}",
        build_body=_seedream5_pro_body,
        tags=("photorealistic", "professional", "featured"),
    ),
    "runway": ModelSpec(
        id="runway",
        provider="runway",
        label="RunWay Text-to-Image",
        create_path="/v1/ai/text-to-image/runway",
        status_path="/v1/ai/text-to-image/runway/{task_id}",
        build_body=_runway_body,
        tags=("photorealistic", "artistic", "featured", "general"),
    ),
    "classic-fast": ModelSpec(
        id="classic-fast",
        provider="magnific",
        label="Magnific Classic Fast",
        create_path="/v1/ai/text-to-image",
        status_path="",  # sync_base64 -- no polling, see response_kind
        build_body=_classic_fast_body,
        tags=("fast", "general", "inline"),
        response_kind="sync_base64",
    ),
}

# New briefs must never silently begin on Magnific's own Mystic model.
# ``auto`` is resolved to one of the third-party Google models below; Mystic
# remains callable only as the technical-failure fallback or an explicit choice.
DEFAULT_MODEL_ID = "imagen4-ultra"

# Legacy Mystic-only sub-styles (see shared.MYSTIC_MODELS) stay valid ONLY
# when the chosen model id is "mystic" -- they are Mystic's own style
# parameter, not a competing model id, so they are validated separately in
# shared.is_valid_model and passed through create_mystic_job unchanged.


def is_known_model(model_id: str) -> bool:
    return model_id == "" or model_id in MODELS


def get_model(model_id: str) -> ModelSpec:
    return MODELS.get(model_id or DEFAULT_MODEL_ID, MODELS[DEFAULT_MODEL_ID])


# --------------------------- auto picker ---------------------------

_PHOTOREAL_HINTS = (
    "photo", "photoreal", "realistic", "photograph", "product shot",
    "ecommerce", "e-commerce", "lifestyle", "professional photo",
)
_PORTRAIT_HINTS = (
    "portrait", "person", "people", "face", "team", "staff", "worker",
    "customer", "client",
)
_ILLUSTRATIVE_HINTS = (
    "illustration", "illustrative", "diagram", "concept art", "icon",
    "infographic", "cartoon", "sketch",
)


def pick_model(role: str, prompt: str, style_direction: str) -> str:
    """Choose a model id automatically, mirroring Magnific's Auto mode goal
    (match model to job) with a transparent, inspectable heuristic instead
    of an unreplicable black box.

    Rules, in priority order:
    1. Portrait/people cues -> Gemini 2.5 Flash.
    2. Featured role -> Imagen 4 Ultra.
    3. Inline/illustrative/diagram and all remaining work -> Imagen 4 Fast.

    The policy intentionally returns a third-party model in every automatic
    case. Mystic is *not* a quality default: handlers retry it only after the
    selected third-party endpoint fails technically.
    """
    text = f"{prompt} {style_direction}".lower()

    if any(h in text for h in _PORTRAIT_HINTS):
        return "gemini-2.5-flash"
    if role == "featured":
        return "imagen4-ultra"
    return "imagen4-fast"

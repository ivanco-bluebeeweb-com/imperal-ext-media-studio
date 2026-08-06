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
    """One callable model: how to build its Magnific request and where."""
    id: str
    provider: str                 # "magnific" | "google" | ... (who trained it)
    label: str                    # human-readable, for UI/errors
    create_path: str              # POST path, relative to BASE_URL
    status_path: str              # GET path template, {task_id} placeholder
    build_body: Callable[[str], dict]  # prompt -> request body
    tags: tuple[str, ...] = field(default_factory=tuple)  # heuristic hints


def _mystic_body(prompt: str) -> dict:
    return {"prompt": prompt, "num_images": 1}


def _imagen4_body(prompt: str) -> dict:
    # Confirmed fields: docs.magnific.com/api-reference/text-to-image/imagen4-fast/generate
    return {"prompt": prompt, "aspect_ratio": "widescreen_16_9"}


def _gemini_flash_body(prompt: str) -> dict:
    # Confirmed fields: docs.magnific.com/api-reference/text-to-image/post-gemini-2-5-flash-image-preview
    return {"prompt": prompt}


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
}

DEFAULT_MODEL_ID = "mystic"

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
    1. Illustrative/diagram cues -> Mystic (its editorial/flexible styles
       cover this; the other registered models are photoreal-first).
    2. Portrait/people cues -> Gemini 2.5 Flash (Google's docs position
       Gemini/Nano Banana as the people-and-editing-oriented tier).
    3. Featured role + photoreal cues (or no cues at all, since a featured
       hero image benefits most from the higher-fidelity tier) -> Imagen 4
       Ultra.
    4. Inline role + photoreal cues -> Imagen 4 Fast (cheaper/faster, and
       Magnific's own docs position Fast for "rapid iteration and cost-
       effective batch generation" -- exactly an inline slot's job).
    5. Anything else -> Mystic, the safe general-purpose default.
    """
    text = f"{prompt} {style_direction}".lower()

    if any(h in text for h in _ILLUSTRATIVE_HINTS):
        return "mystic"
    if any(h in text for h in _PORTRAIT_HINTS):
        return "gemini-2.5-flash"
    if role == "featured":
        return "imagen4-ultra"
    if any(h in text for h in _PHOTOREAL_HINTS):
        return "imagen4-fast"
    return "mystic"

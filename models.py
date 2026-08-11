"""Pydantic parameter models and SDL return entities.

WHY `role` IS A FREE STRING, NOT AN ENUM.

A brief asks for "featured + N inline" images. The natural fixed vocabulary
is {"featured", "inline_1", "inline_2", ...}. Pydantic enums would force a
migration every time someone wants a fifth inline slot, so `role` stays a
plain string built by `_roles_for(inline_count)` in shared.py, validated
against a simple pattern in the handler instead of the type system.

WHY THE PACKAGE HAS A `provider` FIELD BUT ONLY ONE PROVIDER EXISTS.

The architecture note ("Image / Media app MVP") commits to a provider-
agnostic media package model on purpose -- Magnific is the first backend,
Gemini/others may plug in later. Keeping `provider` on the package/asset now
costs nothing and avoids a schema migration when a second provider lands.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from imperal_sdk import sdl


# --------------------------- parameters ---------------------------

class NoParams(BaseModel):
    """Explicit empty params model -- V17 disallows untyped handlers, so
    even a tool with nothing to configure needs a typed (if empty) model."""
    pass


class ConnectMagnificParams(BaseModel):
    api_key: str = Field(
        "", description="Magnific API key to validate and save for this user."
    )


class ProviderConnection(sdl.Entity):
    provider: str = "magnific"
    connected: bool = False
    detail: str = ""


class CreateMediaBriefParams(BaseModel):
    site: str = Field(
        "", description="Which site this article is for, e.g. 'g4s.md'. "
                        "Free text -- used for filtering, not validated against "
                        "a connected-sites list.")
    article_title: str = Field(
        "", description="The article's working title.")
    summary: str = Field(
        "", description="Short summary of the article's content/angle -- used "
                        "as the basis for the image prompt(s).")
    style_direction: str = Field(
        "", description="Optional style guidance, e.g. 'industrial, realistic, "
                        "no text in image, blue/grey palette'.")
    text_policy: str = Field(
        "no_text", description="Whether generated images may render legible "
                        "in-image text: 'no_text' (default -- clean, text-free "
                        "compositions) or 'allow_text' (renders the EXACT words "
                        "given in `image_text` baked into the image, e.g. a "
                        "price or a comparison label). Content Strategy Hub "
                        "decides this per brief; an approved Visual Profile "
                        "that forbids in-image text always overrides an "
                        "'allow_text' request. 'allow_text' without a non-empty "
                        "`image_text` is rejected -- the prompt must always be "
                        "explicit about EITHER no text OR the specific text, "
                        "never a vague 'maybe some text'.")
    image_text: str = Field(
        "", description="The EXACT short text (a label, price, number, or "
                        "one-line phrase) the image must legibly render. "
                        "Required when text_policy='allow_text'; ignored/must "
                        "be empty for 'no_text'.")
    inline_count: int = Field(
        2, ge=0, le=8, description="How many inline supporting images besides "
                                   "the featured image (0-8).")
    model: str = Field(
        "auto", description="Which model to use for every asset in this brief. "
                        "Default 'auto' selects a third-party model available through "
                        "Magnific (Google Imagen or Gemini) for every asset. Choose a "
                        "specific model when needed. Mystic is reserved for the automatic "
                        "technical-failure fallback or an explicit user choice; it is never "
                        "the silent default.")
    lang: str = Field(
        "", description="The POST's own language code, e.g. 'ru', 'ro', 'en' -- "
                        "used for alt text/caption wording, which must match the "
                        "article's language (unlike the image PROMPT, which is "
                        "always generated in English regardless of this field).")
    native_title: str = Field(
        "", description="The article title written in the post's OWN language "
                        "(lang) -- used ONLY to build default alt text/caption. "
                        "Keep 'article_title' itself in English (it feeds the "
                        "image prompt, which must stay English); pass the real, "
                        "native-language title here so alt text/caption come out "
                        "correct for the post instead of defaulting to English. "
                        "Omit to fall back to article_title (English default, "
                        "same as v1 behaviour).")


class GenerateMediaPackageParams(BaseModel):
    package_id: str = Field(
        "", description="The media package to generate images for. Get this "
                        "from create_media_brief or list_media_packages.")


class ListMediaPackagesParams(BaseModel):
    site: str = Field(
        "", description="Filter by site. Omit to list all.")
    status: str = Field(
        "", description="Filter by status: draft, generating, ready, failed. "
                        "Omit for all statuses.")
    limit: int = Field(50, ge=1, le=200)


class GetMediaPackageParams(BaseModel):
    package_id: str = Field("", description="The media package id.")


class RegenerateAssetParams(BaseModel):
    package_id: str = Field("", description="The media package id.")
    role: str = Field(
        "", description="Which asset to regenerate, e.g. 'featured' or "
                        "'inline_1'. Get valid roles from get_media_package.")
    prompt_override: str = Field(
        "", description="Optional replacement prompt for just this asset. "
                        "Omit to reuse the brief's original prompt for this role.")
    model: str = Field(
        "", description="Optional model override for just this asset. Same "
                        "vocabulary as create_media_brief's model field "
                        "(Mystic style, a specific model id, or 'auto'). Omit "
                        "to reuse the package's model (or Mystic's default).")


class GenerateAssetUpscaleParams(BaseModel):
    package_id: str = Field("", description="The media package that owns the image.")
    role: str = Field("", description="Which asset to upscale, e.g. 'featured'.")
    scale_factor: str = Field(
        "2x", description="Magnific Creative Upscaler scale factor: 2x, 4x, 8x, or 16x."
    )


class UpdateAssetMetaParams(BaseModel):
    package_id: str = Field("", description="The media package id.")
    role: str = Field("", description="Which asset to edit, e.g. 'featured'.")
    alt_text: str = Field("", description="New alt text. Omit to keep unchanged.")
    caption: str = Field("", description="New caption. Omit to keep unchanged.")


class DeleteMediaPackageParams(BaseModel):
    package_id: str = Field("", description="The media package id to delete.")


class CheckNewModelsParams(BaseModel):
    """No inputs -- this always checks the one known source (Magnific's
    own sitemap) against the one known registry (model_registry.MODELS)."""
    pass


class ListModelDiscoveryLogParams(BaseModel):
    limit: int = Field(30, ge=1, le=100)


# --------------------------- SDL entities ---------------------------

class DeleteResult(sdl.Entity):
    """Outcome of a delete, phrased so the narrator can state what changed."""
    deleted: bool = False

class MediaAsset(sdl.Entity):
    """One image within a package -- featured or one of the inline slots."""
    role: str = ""              # "featured" | "inline_1" | "inline_2" | ...
    status: str = ""            # "pending" | "generating" | "ready" | "failed"
    image_text: str = ""        # exact text rendered in-image when text_policy=allow_text; "" for no_text
    provider: str = ""          # "magnific" (first and, for now, only backend)
    provider_task_id: str = ""
    model: str = ""              # Mystic model used, "" = provider default
    image_url: str = ""          # final image: original when no upscale ran, otherwise upscaled
    original_image_url: str = "" # provider output before optional auto-upscale
    original_dimensions: str = ""# verified `WIDTH × HEIGHT px`, never guessed from a URL
    original_format: str = ""    # verified PNG/JPEG/WebP format
    upscaled_image_url: str = "" # result from Upscaler Creative; empty when no upscale ran
    upscaled_dimensions: str = ""# verified `WIDTH × HEIGHT px`
    upscaled_format: str = ""    # verified PNG/JPEG/WebP format
    filename: str = ""           # SEO/AEO-optimized base filename (no extension) -- carried through to the site's upload so the LIVE file name is never the provider's raw generated id
    prompt: str = ""
    alt_text: str = ""
    caption: str = ""
    error_message: str = ""


class MediaPackage(sdl.Entity):
    """A brief plus its generated (or in-progress) image assets."""
    site: str = ""
    article_title: str = ""
    summary: str = ""
    style_direction: str = ""
    text_policy: str = "no_text"  # "no_text" | "allow_text" -- see CreateMediaBriefParams.text_policy
    image_text: str = ""        # exact text rendered in-image when text_policy=allow_text -- see CreateMediaBriefParams.image_text
    status: str = ""            # "draft" | "generating" | "ready" | "failed"
    inline_count: int = 0
    model: str = ""              # Mystic model for this brief, "" = provider default
    lang: str = ""                # post's own language, e.g. 'ru'/'ro' -- alt/caption wording
    native_title: str = ""        # article title in lang, used for alt/caption only
    assets: list[MediaAsset] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


class ModelDiscoveryFinding(sdl.Entity):
    """One docs page the sitemap has that model_registry.MODELS doesn't --
    a candidate model, NOT yet a usable one (see model_discovery.py for why
    this never becomes a ModelSpec by itself)."""
    slug: str = ""
    docs_url: str = ""


class ModelDiscoveryResult(sdl.Entity):
    """Outcome of one daily/manual check run."""
    checked_at: str = ""
    source_reachable: bool = False
    known_model_count: int = 0
    new_candidates: list[ModelDiscoveryFinding] = Field(default_factory=list)
    note: str = ""


class ModelDiscoveryLogEntry(sdl.Entity):
    """One past check, for `list_model_discovery_log` -- always recorded,
    even a run that reachably found nothing new."""
    checked_at: str = ""
    source_reachable: bool = False
    new_candidate_slugs: list[str] = Field(default_factory=list)
    note: str = ""

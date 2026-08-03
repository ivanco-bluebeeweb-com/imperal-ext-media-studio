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
    inline_count: int = Field(
        2, ge=0, le=8, description="How many inline supporting images besides "
                                   "the featured image (0-8).")
    model: str = Field(
        "", description="Optional Magnific Mystic model for every asset in "
                        "this brief: 'realism', 'fluid', 'zen', 'flexible', "
                        "'super_real', or 'editorial_portraits'. Omit to use "
                        "Mystic's own default model (unchanged v1 behaviour).")


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
        "", description="Optional Magnific Mystic model override for just this "
                        "asset: 'realism', 'fluid', 'zen', 'flexible', "
                        "'super_real', or 'editorial_portraits'. Omit to reuse "
                        "the package's model (or Mystic's default).")


class UpdateAssetMetaParams(BaseModel):
    package_id: str = Field("", description="The media package id.")
    role: str = Field("", description="Which asset to edit, e.g. 'featured'.")
    alt_text: str = Field("", description="New alt text. Omit to keep unchanged.")
    caption: str = Field("", description="New caption. Omit to keep unchanged.")


class DeleteMediaPackageParams(BaseModel):
    package_id: str = Field("", description="The media package id to delete.")


# --------------------------- SDL entities ---------------------------

class DeleteResult(sdl.Entity):
    """Outcome of a delete, phrased so the narrator can state what changed."""
    deleted: bool = False

class MediaAsset(sdl.Entity):
    """One image within a package -- featured or one of the inline slots."""
    role: str = ""              # "featured" | "inline_1" | "inline_2" | ...
    status: str = ""            # "pending" | "generating" | "ready" | "failed"
    provider: str = ""          # "magnific" (first and, for now, only backend)
    provider_task_id: str = ""
    model: str = ""              # Mystic model used, "" = provider default
    image_url: str = ""
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
    status: str = ""            # "draft" | "generating" | "ready" | "failed"
    inline_count: int = 0
    model: str = ""              # Mystic model for this brief, "" = provider default
    assets: list[MediaAsset] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

"""Safe recovery of legacy provider-hosted Media Hub images.

Older packages retained Magnific's short-lived CDN URL.  Magnific's read-only
Creations API returns the real creation record, including the identifiers that
produced it.  Recovery compares those identifiers with the stable UUID already
embedded in the old URL, then copies the exact source bytes into Imperal
storage.  It never regenerates an image and never guesses from a prompt.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

import magnific_client as mc
import model_registry as mr


CREATIONS_RECENT_PATH = "/v1/creations/recent"
_IDENTITY_KEYS = {"id", "reference", "external_id", "identifier", "task_id", "creation_id"}
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for child in value for item in _strings(child)]
    if isinstance(value, dict):
        return [item for child in value.values() for item in _strings(child)]
    return []


def _identity_tokens(value) -> set[str]:
    """Stable identifiers from URLs or documented creation-record fields."""
    tokens: set[str] = set()
    if isinstance(value, str):
        parsed = urlparse(value)
        tokens.update(match.group(0).lower() for match in _UUID_RE.finditer(value))
        tokens.update(part.lower() for part in parsed.path.split("/") if len(part) >= 8)
        for key, values in parse_qs(parsed.query).items():
            if key.lower() in _IDENTITY_KEYS:
                tokens.update(item.lower() for item in values if len(item) >= 4)
        return tokens
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in _IDENTITY_KEYS:
                tokens.update(item.lower() for item in _strings(child) if len(item) >= 4)
            tokens.update(_identity_tokens(child))
    elif isinstance(value, list):
        for child in value:
            tokens.update(_identity_tokens(child))
    return tokens


def task_ids_from_asset(asset: dict) -> list[str]:
    """Return unique source task ids retained by an asset or its old URL."""
    raw_ids = [asset.get("provider_task_id", "")]
    raw_ids.extend(_UUID_RE.findall(asset.get("original_image_url") or asset.get("image_url", "")))
    return list(dict.fromkeys(item for item in raw_ids if item))


def _creation_urls(record: dict) -> list[str]:
    """Prefer the documented full creation URL, never a thumbnail or preview."""
    creation = record.get("creation")
    if isinstance(creation, dict) and isinstance(creation.get("url"), str):
        return [creation["url"]]
    urls: list[str] = []
    for value in _strings(record):
        if value.startswith("https://") and "thumbnail" not in value and "preview" not in value:
            urls.append(value)
    return list(dict.fromkeys(urls))


def _task_status_paths(preferred_model: str) -> list[str]:
    """Every documented task endpoint, with the recorded model first."""
    paths: list[str] = []
    if preferred_model == "mystic":
        paths.append(mc.CREATE_PATH)
    else:
        spec = mr.MODELS.get(preferred_model)
        if spec and spec.status_path:
            paths.append(spec.status_path.removesuffix("/{task_id}"))
    paths.append(mc.CREATE_PATH)
    paths.extend(
        spec.status_path.removesuffix("/{task_id}")
        for spec in mr.MODELS.values()
        if spec.status_path
    )
    return list(dict.fromkeys(paths))


async def get_provider_task_image_url(ctx, api_key: str, model: str, task_id: str) -> str:
    """Find one historic task through documented model status endpoints only.

    Some early packages did not persist their model.  We can still safely
    identify their original task: every candidate call is a read of the exact
    saved task ID, and only a completed task with exactly one output is used.
    A 404 merely means that this task belongs to another documented model.
    """
    for path in _task_status_paths(model):
        try:
            result = await mc.get_model_task(ctx, api_key, path, task_id)
        except mc.ProviderError:
            continue
        if result.get("state") != "done":
            continue
        urls = list(dict.fromkeys(result.get("image_urls", [])))
        if len(urls) == 1:
            return urls[0]
    return ""


async def list_recent_creations(ctx, api_key: str, *, max_pages: int = 20) -> list[dict]:
    """Read the authenticated user's recent creations without consuming credits."""
    records: list[dict] = []
    for page in range(1, max_pages + 1):
        response = await ctx.http.get(
            f"{mc.BASE_URL}{CREATIONS_RECENT_PATH}",
            headers=mc._headers(api_key),
            params={"page": page, "per_page": 100},
            timeout=30,
        )
        if not (200 <= response.status_code < 300):
            raise mc.ProviderError(
                f"Magnific creations lookup failed (HTTP {response.status_code}).",
                "MEDIA_PROVIDER_ERROR",
            )
        body = response.json()
        batch = body.get("data", []) if isinstance(body, dict) else []
        if not isinstance(batch, list) or not all(isinstance(item, dict) for item in batch):
            raise mc.ProviderError(
                "Magnific creations lookup returned an unrecognizable response.",
                "MEDIA_PROVIDER_ERROR",
            )
        records.extend(batch)
        if len(batch) < 100:
            break
    return records


def match_creation_urls(assets: list[dict], creations: list[dict]) -> dict[str, str]:
    """Return an exact source URL only where creation identities prove a match."""
    matches: dict[str, str] = {}
    for asset in assets:
        legacy_url = asset.get("original_image_url") or asset.get("image_url", "")
        asset_tokens = _identity_tokens(legacy_url)
        task_id = asset.get("provider_task_id", "")
        if task_id:
            asset_tokens.add(task_id.lower())
        if not asset_tokens:
            continue
        candidates: list[str] = []
        for creation in creations:
            if not (asset_tokens & _identity_tokens(creation)):
                continue
            candidates.extend(_creation_urls(creation))
        candidates = list(dict.fromkeys(candidates))
        if len(candidates) == 1:
            matches[asset.get("role", "")] = candidates[0]
    return matches

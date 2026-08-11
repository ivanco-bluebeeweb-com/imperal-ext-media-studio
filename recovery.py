"""Recovery of legacy provider-hosted Media Hub images.

Old packages stored Magnific's short-lived CDN link directly.  This module
uses Magnific's read-only Creations API only to find the same creation again,
then lets the normal storage pipeline copy its bytes into Imperal storage.
A match is deliberately exact on the URL filename without query parameters:
we never guess by prompt, date, or visual similarity and therefore never put
the wrong image into an article package.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse
import re

import magnific_client as mc


CREATIONS_RECENT_PATH = "/v1/creations/recent"


def url_filename(url: str) -> str:
    """Return the stable path filename, never an expiring query token."""
    return urlparse(url).path.rsplit("/", 1)[-1]


def _urls_from(value) -> list[str]:
    """Extract all HTTPS URL strings from a provider response defensively."""
    found: list[str] = []
    if isinstance(value, str):
        if value.startswith("https://"):
            found.append(value)
    elif isinstance(value, dict):
        for child in value.values():
            found.extend(_urls_from(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_urls_from(child))
    return found


async def list_recent_creation_urls(ctx, api_key: str, *, max_pages: int = 20) -> list[str]:
    """Read the user's recent Magnific creations without consuming credits.

    The API is paginated newest-first. We stop at the first short page; if a
    provider response omits pagination metadata this remains safe and bounded.
    """
    urls: list[str] = []
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
        records = body.get("data", []) if isinstance(body, dict) else []
        if not isinstance(records, list):
            raise mc.ProviderError(
                "Magnific creations lookup returned an unrecognizable response.",
                "MEDIA_PROVIDER_ERROR",
            )
        for record in records:
            urls.extend(_urls_from(record))
        if len(records) < 100:
            break
    return list(dict.fromkeys(urls))


def _identity_tokens(url: str) -> set[str]:
    """Stable provider identifiers found in a creation URL."""
    parsed = urlparse(url)
    tokens = {part for part in parsed.path.split("/") if len(part) >= 8}
    for key, values in parse_qs(parsed.query).items():
        if key.lower() in {"id", "task_id", "creation_id", "reference", "identifier"}:
            tokens.update(value for value in values if len(value) >= 8)
    tokens.update(re.findall(r"[0-9a-f]{8}-[0-9a-f-]{27,}", url, flags=re.I))
    return tokens


def match_creation_urls(assets: list[dict], creation_urls: list[str]) -> dict[str, str]:
    """Map an asset to its one, proven provider creation.

    An asset task id, where retained, is checked against provider identifiers.
    Older records without one require an unambiguous exact filename. Any
    uncertainty leaves the record unchanged instead of cross-linking media.
    """
    by_name: dict[str, list[str]] = {}
    for url in creation_urls:
        name = url_filename(url)
        if name:
            by_name.setdefault(name, []).append(url)
    matches: dict[str, str] = {}
    for asset in assets:
        legacy_url = asset.get("original_image_url") or asset.get("image_url", "")
        name = url_filename(legacy_url)
        candidates = list(dict.fromkeys(by_name.get(name, [])))
        task_id = asset.get("provider_task_id", "")
        if task_id:
            candidates = [url for url in candidates if task_id in _identity_tokens(url)]
        if name and len(candidates) == 1:
            matches[asset.get("role", "")] = candidates[0]
    return matches

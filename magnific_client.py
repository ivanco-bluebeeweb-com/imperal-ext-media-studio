"""Magnific (Mystic) provider client -- create a text-to-image job and poll it.

WHY POLLING, NOT `@ext.webhook`, IN v1.

We already have a live, confirmed platform bug in a *different* extension
(Asana Connector): the webhook layer does not proxy a handler's HTTP headers/
status code back onto the wire, which breaks challenge/echo handshakes.
Magnific's webhook model is different -- an HMAC-signed POST body, not a
header-echo handshake -- so it MIGHT be unaffected, but that is unverified
end-to-end. Polling has zero dependency on that unverified path and works
today, so v1 polls. A webhook path can be added later once proven safe.

WHY THE RESPONSE PARSING IS DEFENSIVE.

Magnific's public docs (fetched at build time) confirm the two endpoints and
the auth header, but the bot-protected/rendered pages did not yield a full,
guaranteed field-by-field response schema for the task-status body. Rather
than assume field names and silently mis-map a real response, `_extract_*`
below tries several documented-plausible keys and raises a structured
MEDIA_PROVIDER_ERROR (not a silent None) when the shape is unrecognized --
so a real integration run surfaces "provider response didn't match" instead
of quietly losing images.
"""

from __future__ import annotations

import asyncio

BASE_URL = "https://api.magnific.com"
CREATE_PATH = "/v1/ai/mystic"
STATUS_PATH = "/v1/ai/mystic/{task_id}"

# Statuses observed/documented across Magnific's async task endpoints
# (Mystic, Upscaler, video) all follow the same lifecycle vocabulary.
DONE_STATUSES = {"completed", "done", "success", "succeeded"}
FAILED_STATUSES = {"failed", "error", "cancelled", "canceled"}

DEFAULT_POLL_INTERVAL_S = 3
DEFAULT_MAX_POLLS = 40  # ~2 minutes at 3s -- Mystic generation is typically fast


class ProviderError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def _headers(api_key: str) -> dict:
    return {"x-magnific-api-key": api_key, "content-type": "application/json"}


async def validate_api_key(ctx, api_key: str) -> None:
    """Verify a key without generating anything or consuming image credits.

    CORRECTED CHOICE OF ENDPOINT (was a real bug, not a hypothetical one).
    v1 of this validated against ``GET /v1/analytics/team-members``, which
    Magnific's own docs mark "Available exclusively for Business and
    Enterprise plans" -- so a perfectly valid key on any other plan gets a
    403 there, and this code reported that as "the key is invalid", which
    is wrong. ``GET /v1/creations/recent`` (Creations API) has no plan
    restriction documented, resolves identity from the key alone, and
    explicitly "does not consume credits" -- a 2xx there proves the key
    works for any plan, so it replaces the analytics call here.
    """
    resp = await ctx.http.get(
        f"{BASE_URL}/v1/creations/recent",
        headers=_headers(api_key),
        timeout=30,
    )
    if not (200 <= resp.status_code < 300):
        raise ProviderError(
            f"Magnific rejected the API key (HTTP {resp.status_code}).",
            "MEDIA_PROVIDER_KEY_INVALID",
        )


async def create_mystic_job(
    ctx, api_key: str, prompt: str, *, num_images: int = 1, model: str = "",
) -> str:
    """POST /v1/ai/mystic -- returns the provider task id.

    `model` is opt-in and forwarded ONLY when non-empty. Mystic's documented
    behaviour for the field being absent entirely is "use the default model"
    (docs.magnific.com/api-reference/mystic/post-mystic) -- that is exactly
    v1's only behaviour, so omitting the key (not sending `model: ""`) keeps
    every existing caller byte-for-byte unchanged.
    """
    body: dict = {"prompt": prompt, "num_images": num_images}
    if model:
        body["model"] = model
    resp = await ctx.http.post(
        f"{BASE_URL}{CREATE_PATH}",
        headers=_headers(api_key),
        json=body,
        timeout=60,
    )
    if resp.status_code == 401:
        raise ProviderError("Magnific rejected the API key (401).", "MEDIA_PROVIDER_ERROR")
    if not (200 <= resp.status_code < 300):
        raise ProviderError(
            f"Magnific create-job call failed (HTTP {resp.status_code}).",
            "MEDIA_PROVIDER_ERROR",
        )
    body = resp.json()
    task_id = _extract_task_id(body)
    if not task_id:
        raise ProviderError(
            "Magnific accepted the job but returned no recognizable task id.",
            "MEDIA_PROVIDER_ERROR",
        )
    return task_id


async def get_mystic_task(ctx, api_key: str, task_id: str) -> dict:
    """GET /v1/ai/mystic/{task_id} -- returns a normalized status dict."""
    resp = await ctx.http.get(
        f"{BASE_URL}{STATUS_PATH.format(task_id=task_id)}",
        headers=_headers(api_key),
        timeout=30,
    )
    if not (200 <= resp.status_code < 300):
        raise ProviderError(
            f"Magnific status check failed (HTTP {resp.status_code}).",
            "MEDIA_PROVIDER_ERROR",
        )
    body = resp.json()
    status = _extract_status(body)
    if status in DONE_STATUSES:
        urls = _extract_image_urls(body)
        if not urls:
            raise ProviderError(
                "Magnific reported the job as done but returned no image URLs.",
                "MEDIA_PROVIDER_ERROR",
            )
        return {"state": "done", "image_urls": urls, "raw_status": status}
    if status in FAILED_STATUSES:
        return {"state": "failed", "image_urls": [], "raw_status": status}
    return {"state": "pending", "image_urls": [], "raw_status": status or "unknown"}


async def generate_image(
    ctx,
    api_key: str,
    prompt: str,
    *,
    model: str = "",
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    max_polls: int = DEFAULT_MAX_POLLS,
    on_progress=None,
) -> str:
    """Create a Mystic job and poll it to completion. Returns one image URL.

    `on_progress(attempt, max_polls)` is called before each poll so callers
    can surface `ctx.progress(...)` without this module depending on ctx's
    progress API directly (keeps the provider client focused on Magnific,
    not on chat/task plumbing).
    """
    task_id = await create_mystic_job(ctx, api_key, prompt, model=model)
    for attempt in range(1, max_polls + 1):
        if on_progress:
            await on_progress(attempt, max_polls)
        await asyncio.sleep(poll_interval_s)
        result = await get_mystic_task(ctx, api_key, task_id)
        if result["state"] == "done":
            return result["image_urls"][0]
        if result["state"] == "failed":
            raise ProviderError(
                f"Magnific reported the generation job as failed "
                f"(status={result['raw_status']}).",
                "MEDIA_PROVIDER_ERROR",
            )
    raise ProviderError(
        f"Magnific job did not finish within {max_polls * poll_interval_s:.0f}s.",
        "MEDIA_PROVIDER_TIMEOUT",
    )


# --------------------------- generic multi-model path ---------------------------
#
# The functions above are Mystic-specific and stay exactly as they were --
# every existing caller/test keeps working byte-for-byte. Everything below
# is the NEW generic path used for any model registered in model_registry
# (Imagen 4 Fast/Ultra, Gemini 2.5 Flash, and Mystic itself via the same
# generic path once `spec` is passed). Same lifecycle vocabulary, same
# `{"data": {...}}` unwrap convention -- confirmed identical across every
# Magnific async task endpoint in their docs -- so `_extract_*` is reused
# unchanged; only the request path/body differ per model.

async def create_job(ctx, api_key: str, spec, prompt: str) -> str:
    """POST to `spec.create_path` with `spec.build_body(prompt)`. Generic
    counterpart to create_mystic_job -- works for any ModelSpec."""
    resp = await ctx.http.post(
        f"{BASE_URL}{spec.create_path}",
        headers=_headers(api_key),
        json=spec.build_body(prompt),
        timeout=60,
    )
    if resp.status_code == 401:
        raise ProviderError(
            f"Magnific rejected the API key (401) for {spec.label}.",
            "MEDIA_PROVIDER_ERROR",
        )
    if not (200 <= resp.status_code < 300):
        raise ProviderError(
            f"Magnific create-job call failed for {spec.label} "
            f"(HTTP {resp.status_code}).",
            "MEDIA_PROVIDER_ERROR",
        )
    body = resp.json()
    task_id = _extract_task_id(body)
    if not task_id:
        raise ProviderError(
            f"Magnific accepted the {spec.label} job but returned no "
            "recognizable task id.",
            "MEDIA_PROVIDER_ERROR",
        )
    return task_id


async def get_job(ctx, api_key: str, spec, task_id: str) -> dict:
    """GET `spec.status_path` -- generic counterpart to get_mystic_task."""
    resp = await ctx.http.get(
        f"{BASE_URL}{spec.status_path.format(task_id=task_id)}",
        headers=_headers(api_key),
        timeout=30,
    )
    if not (200 <= resp.status_code < 300):
        raise ProviderError(
            f"Magnific status check failed for {spec.label} "
            f"(HTTP {resp.status_code}).",
            "MEDIA_PROVIDER_ERROR",
        )
    body = resp.json()
    status = _extract_status(body)
    if status in DONE_STATUSES:
        urls = _extract_image_urls(body)
        if not urls:
            raise ProviderError(
                f"Magnific reported the {spec.label} job as done but "
                "returned no image URLs.",
                "MEDIA_PROVIDER_ERROR",
            )
        return {"state": "done", "image_urls": urls, "raw_status": status}
    if status in FAILED_STATUSES:
        return {"state": "failed", "image_urls": [], "raw_status": status}
    return {"state": "pending", "image_urls": [], "raw_status": status or "unknown"}


async def generate_image_with_model(
    ctx,
    api_key: str,
    prompt: str,
    spec,
    *,
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    max_polls: int = DEFAULT_MAX_POLLS,
    on_progress=None,
) -> str:
    """Create + poll a job against ANY registered model (`spec` is a
    model_registry.ModelSpec). This is the multi-provider counterpart to
    `generate_image`, which stays Mystic-only for backward compatibility.
    """
    task_id = await create_job(ctx, api_key, spec, prompt)
    for attempt in range(1, max_polls + 1):
        if on_progress:
            await on_progress(attempt, max_polls)
        await asyncio.sleep(poll_interval_s)
        result = await get_job(ctx, api_key, spec, task_id)
        if result["state"] == "done":
            return result["image_urls"][0]
        if result["state"] == "failed":
            raise ProviderError(
                f"Magnific reported the {spec.label} generation job as "
                f"failed (status={result['raw_status']}).",
                "MEDIA_PROVIDER_ERROR",
            )
    raise ProviderError(
        f"Magnific {spec.label} job did not finish within "
        f"{max_polls * poll_interval_s:.0f}s.",
        "MEDIA_PROVIDER_TIMEOUT",
    )


# --------------------------- response shape helpers ---------------------------

def _unwrap(body: dict) -> dict:
    """Magnific (like many REST APIs) may wrap the payload in a `data` key."""
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        return body["data"]
    return body if isinstance(body, dict) else {}


def _extract_task_id(body: dict) -> str:
    d = _unwrap(body)
    for key in ("task_id", "id", "taskId"):
        val = d.get(key)
        if val:
            return str(val)
    return ""


def _extract_status(body: dict) -> str:
    d = _unwrap(body)
    for key in ("status", "task_status", "state"):
        val = d.get(key)
        if val:
            return str(val).lower()
    return ""


def _extract_image_urls(body: dict) -> list[str]:
    d = _unwrap(body)
    for key in ("generated", "output", "images", "results", "urls"):
        val = d.get(key)
        if isinstance(val, list) and val:
            urls = []
            for item in val:
                if isinstance(item, str):
                    urls.append(item)
                elif isinstance(item, dict):
                    u = item.get("url") or item.get("image_url") or item.get("output_url")
                    if u:
                        urls.append(u)
            if urls:
                return urls
    return []

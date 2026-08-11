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
import base64

import httpx

BASE_URL = "https://api.magnific.com"
CREATE_PATH = "/v1/ai/mystic"

# Upscaler Creative -- confirmed via docs.magnific.com/api-reference/
# image-upscaler-creative/{post-image-upscaler,get-image-upscaler}. This is
# the ONLY upscaler endpoint used here (never *-precision or *-precision-v2):
# both Precision endpoints are explicitly documented as "may modify the
# original image content based on the prompt", which is wrong for a
# dimension-only auto-upscale that must not change what the image shows.
UPSCALE_CREATE_PATH = "/v1/ai/image-upscaler"
UPSCALE_STATUS_PATH = "/v1/ai/image-upscaler/{task_id}"
# Documented enum, smallest-first -- upscale_scale_factor_for() below picks
# the smallest one that clears the pipeline's minimum-side threshold.
UPSCALE_SCALE_FACTORS = (2, 4, 8, 16)
# Documented hard cap on the OUTPUT image: "can't exceed maximum allowed
# size of 25.3 million pixels" (post-image-upscaler schema for `image`).
UPSCALE_MAX_OUTPUT_PIXELS = 25_300_000
STATUS_PATH = "/v1/ai/mystic/{task_id}"


def available_upscale_scale_factors() -> tuple[str, ...]:
    """Magnific Creative Upscaler's documented `scale_factor` enum, formatted
    for forms and API validation from the same canonical client constant."""
    return tuple(f"{factor}x" for factor in UPSCALE_SCALE_FACTORS)

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
    aspect_ratio: str = "",
) -> str:
    """POST /v1/ai/mystic -- returns the provider task id.

    `model` is opt-in and forwarded ONLY when non-empty. Mystic's documented
    behaviour for the field being absent entirely is "use the default model"
    (docs.magnific.com/api-reference/mystic/post-mystic) -- that is exactly
    v1's only behaviour, so omitting the key (not sending `model: ""`) keeps
    every existing caller byte-for-byte unchanged.

    `aspect_ratio` is likewise opt-in and forwarded ONLY when non-empty --
    confirmed as a real Mystic field (default `square_1_1`) on the same docs
    page. Callers that don't pass it keep getting Mystic's own default,
    exactly like v1; the pipeline's blogpost callers now pass
    shared.ASPECT_RATIO_4_3 explicitly (see shared.prompt_for_role's sibling
    constant) to satisfy the 4:3 landscape standing requirement.
    """
    body: dict = {"prompt": prompt, "num_images": num_images}
    if model:
        body["model"] = model
    if aspect_ratio:
        body["aspect_ratio"] = aspect_ratio
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


async def get_model_task(ctx, api_key: str, model_path: str, task_id: str) -> dict:
    """Read one task from a documented Magnific model endpoint."""
    resp = await ctx.http.get(
        f"{BASE_URL}{model_path}/{task_id}",
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


async def get_mystic_task(ctx, api_key: str, task_id: str) -> dict:
    """GET /v1/ai/mystic/{task_id} -- normalized historic Mystic task status."""
    return await get_model_task(ctx, api_key, CREATE_PATH, task_id)


async def generate_image(
    ctx,
    api_key: str,
    prompt: str,
    *,
    model: str = "",
    aspect_ratio: str = "",
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
    task_id = await create_mystic_job(
        ctx, api_key, prompt, model=model, aspect_ratio=aspect_ratio,
    )
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


async def create_sync_image(ctx, api_key: str, spec, prompt: str) -> str:
    """POST `spec.create_path` for a `response_kind == "sync_base64"` model
    (currently only Classic Fast) and return a hosted image URL.

    Confirmed via docs.magnific.com/api-reference/text-to-image/get-image-from-text:
    this endpoint answers in ONE call with `{"data": [{"base64": "..."}]}` --
    no task id, nothing to poll. Magnific gives us raw bytes, not a URL, so
    this uploads them through `ctx.storage` (the same mechanism every other
    model relies on Magnific's own CDN for) to produce a URL the rest of the
    pipeline (alt text, WordPress upload, etc.) can treat identically.
    """
    import base64
    import time
    import uuid

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
    d = _unwrap(body)
    items = d.get("data") if isinstance(d, dict) else None
    b64 = None
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and item.get("base64"):
                b64 = item["base64"]
                break
    if not b64:
        raise ProviderError(
            f"Magnific accepted the {spec.label} request but returned no "
            "image data.",
            "MEDIA_PROVIDER_ERROR",
        )
    try:
        raw = base64.b64decode(b64)
    except Exception as exc:
        raise ProviderError(
            f"Magnific's {spec.label} response image data could not be "
            f"decoded: {exc}.",
            "MEDIA_PROVIDER_ERROR",
        )
    path = f"media-studio/{spec.id}-{uuid.uuid4().hex[:12]}-{int(time.time())}.png"
    info = await ctx.storage.upload(path, raw, content_type="image/png")
    if not info.url:
        raise ProviderError(
            f"Uploaded the {spec.label} image but storage returned no URL.",
            "MEDIA_PROVIDER_ERROR",
        )
    return info.url


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

    `response_kind == "sync_base64"` models (Classic Fast) skip polling
    entirely -- there is no task id to poll -- and go through
    `create_sync_image` instead.
    """
    if getattr(spec, "response_kind", "async_url") == "sync_base64":
        if on_progress:
            await on_progress(1, 1)
        return await create_sync_image(ctx, api_key, spec, prompt)

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


# --------------------------- Upscaler Creative (auto-upscale) ---------------------------
#
# WHY THIS DOWNLOADS BYTES WITH A PLAIN httpx.AsyncClient, NOT ctx.http.
#
# Confirmed live during development: the federal ctx.http client always
# decodes a non-JSON response body through `resp.text` (see imperal_sdk's
# HTTPClient._wrap) -- i.e. it treats binary image bytes as UTF-8 text. On a
# real PNG/JPEG this corrupts the bytes (any byte >= 0x80 that isn't valid
# UTF-8 gets replaced with U+FFFD), breaking both the PNG/JPEG header this
# module needs to read AND the base64 payload the upscaler API requires.
# Imperal's own docs (recipes/handle-user-api-keys) show a raw
# `httpx.AsyncClient()` call inside a handler as a legitimate pattern
# ("discouraged by convention, not banned by a validator") specifically for
# this kind of binary/non-JSON traffic, and httpx is already a transitive
# dependency of imperal-sdk itself -- so this adds no new dependency.
async def download_image_bytes(url: str, *, timeout: float = 60) -> bytes:
    """Fetch raw bytes from a URL (Magnific/Freepik's CDN) without corrupting
    binary content. Raises ProviderError on any non-2xx response."""
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        resp = await client.get(url)
    if not (200 <= resp.status_code < 300):
        raise ProviderError(
            f"Could not download the generated image for size checking "
            f"(HTTP {resp.status_code}).",
            "MEDIA_PROVIDER_ERROR",
        )
    return resp.content


def upscale_scale_factor_for(width: int, height: int, *, min_side: int = 1500) -> str | None:
    """Pick the smallest documented scale_factor (2x/4x/8x/16x) that brings
    the SMALLER side up to at least `min_side`, respecting the upscaler's
    documented 25.3-megapixel output cap. Returns None when the image
    already clears `min_side` on both sides, or when no legal factor can
    clear it without breaching the output cap (skip, don't force a request
    that Magnific would reject).

    Only the smaller side needs to reach `min_side` -- "меньше чем 1500
    пикселей в любую сторону" (the standing directive) means the trigger is
    whichever side is smallest.
    """
    smaller_side = min(width, height)
    if smaller_side >= min_side:
        return None
    for factor in UPSCALE_SCALE_FACTORS:
        if smaller_side * factor < min_side:
            continue
        if (width * factor) * (height * factor) > UPSCALE_MAX_OUTPUT_PIXELS:
            continue
        return f"{factor}x"
    return None


async def create_upscale_job(ctx, api_key: str, image_bytes: bytes, scale_factor: str) -> str:
    """POST /v1/ai/image-upscaler (Creative). `image` is REQUIRED base64 --
    confirmed via the endpoint's own schema (type=string, format=byte); there
    is no URL-input alternative on this field despite the "Image Input Best
    Practices" guide recommending a URL when calling other Magnific tools --
    that guide describes how developers OBTAIN good base64 (read the file
    directly rather than re-encoding a canvas), not a different wire format.
    """
    body = {
        "image": base64.b64encode(image_bytes).decode("ascii"),
        "scale_factor": scale_factor,
    }
    resp = await ctx.http.post(
        f"{BASE_URL}{UPSCALE_CREATE_PATH}",
        headers=_headers(api_key),
        json=body,
        timeout=60,
    )
    if resp.status_code == 401:
        raise ProviderError(
            "Magnific rejected the API key (401) for the image upscaler.",
            "MEDIA_PROVIDER_ERROR",
        )
    if not (200 <= resp.status_code < 300):
        raise ProviderError(
            f"Magnific upscaler create-job call failed (HTTP {resp.status_code}).",
            "MEDIA_PROVIDER_ERROR",
        )
    task_id = _extract_task_id(resp.json())
    if not task_id:
        raise ProviderError(
            "Magnific accepted the upscale request but returned no task id.",
            "MEDIA_PROVIDER_ERROR",
        )
    return task_id


async def get_upscale_job(ctx, api_key: str, task_id: str) -> dict:
    """GET /v1/ai/image-upscaler/{task-id} -- same lifecycle vocabulary and
    `{"data": {"generated": [...], "status": ...}}` wrapping as every other
    Magnific async task endpoint, so the shared _extract_* helpers apply
    unchanged."""
    resp = await ctx.http.get(
        f"{BASE_URL}{UPSCALE_STATUS_PATH.format(task_id=task_id)}",
        headers=_headers(api_key),
        timeout=30,
    )
    if not (200 <= resp.status_code < 300):
        raise ProviderError(
            f"Magnific upscaler status check failed (HTTP {resp.status_code}).",
            "MEDIA_PROVIDER_ERROR",
        )
    body = resp.json()
    status = _extract_status(body)
    if status in DONE_STATUSES:
        urls = _extract_image_urls(body)
        if not urls:
            raise ProviderError(
                "Magnific reported the upscale job as done but returned no "
                "image URLs.",
                "MEDIA_PROVIDER_ERROR",
            )
        return {"state": "done", "image_urls": urls, "raw_status": status}
    if status in FAILED_STATUSES:
        return {"state": "failed", "image_urls": [], "raw_status": status}
    return {"state": "pending", "image_urls": [], "raw_status": status or "unknown"}


async def upscale_image(
    ctx, api_key: str, image_bytes: bytes, scale_factor: str, *,
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    max_polls: int = DEFAULT_MAX_POLLS,
) -> str:
    """Create + poll an Upscaler Creative job and return the resulting image
    URL. Raises ProviderError on failure or timeout -- callers decide whether
    that should fail the whole asset or just keep the original image."""
    task_id = await create_upscale_job(ctx, api_key, image_bytes, scale_factor)
    for _ in range(max_polls):
        await asyncio.sleep(poll_interval_s)
        result = await get_upscale_job(ctx, api_key, task_id)
        if result["state"] == "done":
            return result["image_urls"][0]
        if result["state"] == "failed":
            raise ProviderError(
                f"Magnific reported the upscale job as failed "
                f"(status={result['raw_status']}).",
                "MEDIA_PROVIDER_ERROR",
            )
    raise ProviderError(
        f"Magnific upscale job {task_id} did not finish within "
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

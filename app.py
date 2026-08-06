"""Extension declaration, secrets, lifecycle hooks.

WHY BYOK (bring-your-own-key), not a platform-run OAuth or a shared key.

Magnific's API is gated behind their own Business/Enterprise subscription --
it is a service the USER pays Magnific for directly, not something Imperal
can broker centrally. So this extension follows the same pattern as the
Spotify recipe in the platform docs: the user pastes their own Magnific API
key (and webhook signing secret) once, Vault-encrypted via `ctx.secrets`,
and every call runs against their own quota. There is no OAuth dance here --
Magnific issues static API keys from its dashboard, not a token exchange.

Two secrets, not one, because Magnific hands out both at the same time from
the same "Create API key" step and they serve different purposes:
  * `magnific_api_key`      -- sent as `x-magnific-api-key` on every request.
  * `magnific_webhook_secret` -- would verify inbound webhook signatures IF
    this extension used webhooks. v1 deliberately polls instead (see
    magnific_client.py for why), so the secret is stored for forward
    compatibility but not read by any v1 handler.
"""

from imperal_sdk import Extension, ChatExtension

ext = Extension(
    "media-studio",
    version="1.1.0",
    display_name="Media Hub",
    description=(
        "Turn an article brief into a ready-to-publish image package -- "
        "featured image plus inline supporting images, with alt text -- using "
        "your own Magnific API key. Supports multiple AI image models "
        "(Magnific Mystic, Google Imagen 4 Fast/Ultra, Google Gemini 2.5 "
        "Flash) plus an automatic 'auto' model picker that matches the model "
        "to the image's role and prompt."
    ),
    icon="icon.svg",
    actions_explicit=True,
    capabilities=["media:read", "media:write"],
)

chat = ChatExtension(
    ext,
    tool_name="media-studio",
    description="Generate and manage AI image packages for articles",
)

ext.secret(
    name="magnific_api_key",
    description=(
        "Your Magnific API key -- Magnific dashboard -> Organization "
        "Settings -> API Keys -> Create API key. Requires a Business or "
        "Enterprise Magnific plan."
    ),
    required=True,
    # "both": the built-in Secrets screen remains available, while Media
    # Studio's own Connect flow can validate a pasted key before saving it.
    write_mode="both",
    max_bytes=200,
    rotation_hint_days=90,
)(lambda: None)

ext.secret(
    name="magnific_webhook_secret",
    description=(
        "Webhook signing secret shown alongside your Magnific API key. "
        "Stored for future use -- v1 polls task status instead of relying "
        "on inbound webhooks, so this is not read by any handler yet."
    ),
    required=False,
    write_mode="user",
    max_bytes=200,
    rotation_hint_days=90,
)(lambda: None)


@ext.health_check
async def health_check(ctx) -> dict:
    """Liveness probe for the extension."""
    return {"status": "ok"}

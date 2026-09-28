"""Provider connect/manage tools -- one module per concern, not folded into
handlers.py, because this is explicitly the seam where a second provider
(Gemini, etc.) plugs in later. `list_providers` already returns a list so
adding a second entry never requires a shape change, only a second
`ProviderConnection` built the same way `magnific` is built here.

WHY connect_magnific VALIDATES BEFORE SAVING, AND WHY THE SECRET IS
write_mode="both".

`magnific_api_key` was declared `write_mode="user"`, which means only the
platform's own generic Secrets screen could ever write it -- extension code
calling `ctx.secrets.set()` raises `SecretWriteForbidden` (confirmed by
reading `imperal_sdk/secrets/client.py`). That is workable, but it leaves a
first-time user with no in-app screen that explains what a Magnific API key
even is, where to get one, or whether the one they pasted actually works --
exactly the gap that prompted this rewrite. Asana Connector and Notion
Connector solve it the same way: declare `write_mode="both"` so the platform
Secrets screen keeps working AND this extension's own `connect_magnific`
can validate the key against Magnific's API *before* writing it, so a bad
paste is rejected immediately instead of failing silently on first use.
"""

from __future__ import annotations

from imperal_sdk import ActionResult, sdl

import codes as c
import magnific_client as mc
from app import chat, ext
from models import ConnectMagnificParams, NoParams, ProviderConnection
from shared import error as _error

# Registry of providers this extension knows how to manage. Today there is
# exactly one; a second provider adds one more entry here and to
# panels.py's provider list -- no other file changes.
_KNOWN_PROVIDERS = ("magnific",)


async def _magnific_connection(ctx) -> ProviderConnection:
    is_set = await ctx.secrets.is_set("magnific_api_key")
    return ProviderConnection(
        id="magnific",
        title="Magnific (Mystic)",
        provider="magnific",
        connected=is_set,
        detail=(
            "Connected -- ready to generate images."
            if is_set else
            "Not connected -- paste an API key to enable image generation."
        ),
    )


async def list_provider_connections(ctx) -> list[ProviderConnection]:
    """Used by both the `list_providers` tool and the Providers panel."""
    return [await _magnific_connection(ctx)]


async def magnific_live_status(ctx) -> str:
    """Live probe against Magnific itself -- 'not_connected' | 'connected' |
    'reconnect_required'. Mirrors Google Analytics's handlers_accounts.py
    live_status(): a saved key is not proof it still works (Magnific can
    revoke/expire it any time, see Task #2236's silent-stall case), only an
    actual call against Magnific's own API proves that.

    Deliberately a SEPARATE function from `_magnific_connection` (used by
    list_providers/connect/disconnect): those must stay a cheap is_set()
    check with no network call and no behaviour change, since chat tools
    and their tests assert on that exact fast shape. This one exists solely
    to drive the sidebar warning banner (packages_nav_panel), the same way
    GA4's live_status exists solely to drive its own sidebar Alert.
    """
    api_key = await ctx.secrets.get("magnific_api_key")
    if not api_key:
        return "not_connected"
    try:
        await mc.validate_api_key(ctx, api_key)
    except mc.ProviderError:
        return "reconnect_required"
    return "connected"


@chat.function(
    "list_providers",
    "List every image-generation provider Media Hub knows about, and "
    "whether each one is connected for this user.",
    action_type="read",
    data_model=ProviderConnection,
    event="media-studio.list_providers",
)
async def list_providers(ctx, params: NoParams) -> ActionResult:
    """Report connection status for every known provider."""
    rows = await list_provider_connections(ctx)
    connected = sum(1 for r in rows if r.connected)
    return ActionResult.success(
        sdl.EntityList(items=rows),
        f"{connected}/{len(rows)} provider(s) connected.",
    )


@chat.function(
    "connect_magnific",
    "Connect Magnific by saving your API key, after checking it actually "
    "works. Get a key from magnific.com: User menu -> Organization Settings "
    "-> API Keys (requires a Business or Enterprise plan).",
    action_type="write",
    chain_callable=True,
    data_model=ProviderConnection,
    event="media-studio.connect_magnific",
    effects=["media-studio.provider.connected"],
)
async def connect_magnific(ctx, params: ConnectMagnificParams) -> ActionResult:
    """Validate-then-store: a key Magnific rejects is never written, so the
    stored value can never be one we already know is broken."""
    api_key = params.api_key.strip()
    if not api_key:
        return _error(
            "No API key was provided. Create one at magnific.com -- User "
            "menu -> Organization Settings -> API Keys.",
            c.MEDIA_KEY_NOT_CONFIGURED,
        )

    try:
        await mc.validate_api_key(ctx, api_key)
    except mc.ProviderError as exc:
        return _error(str(exc), exc.code)

    await ctx.secrets.set("magnific_api_key", api_key)
    webhook_secret = (params.webhook_secret or "").strip()
    if webhook_secret:
        await ctx.secrets.set("magnific_webhook_secret", webhook_secret)
    connection = await _magnific_connection(ctx)
    return ActionResult.success(
        connection, "Magnific connected -- the key was verified before saving.",
    )


@chat.function(
    "disconnect_magnific",
    "Disconnect Magnific: deletes the saved API key. Existing media "
    "packages and their already-generated images are kept -- only new "
    "generation is blocked until you connect again.",
    action_type="write",
    data_model=ProviderConnection,
    event="media-studio.disconnect_magnific",
    effects=["media-studio.provider.disconnected"],
)
async def disconnect_magnific(ctx, params: NoParams) -> ActionResult:
    """Delete the stored key. Package/asset records are untouched."""
    await ctx.secrets.delete("magnific_api_key")
    connection = await _magnific_connection(ctx)
    return ActionResult.success(connection, "Magnific disconnected.")

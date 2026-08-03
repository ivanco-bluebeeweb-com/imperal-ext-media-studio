# Media Studio

Generates a featured image plus supporting inline images for an article,
using [Magnific](https://magnific.com)'s Mystic text-to-image model as the
first (and currently only) image provider. Built as a standalone,
provider-agnostic media layer for the G4S/Climtec article pipeline — not
folded into WP Site Connector or SEO Audit Engine, so a second provider or a
second consumer app can plug in later without moving code.

## Connecting

Media Studio needs a Magnific API key. There are two ways to set it, and
both write the same secret:

1. **In-app Connect screen (recommended)** — open the center panel; a
   first-time user with no provider connected lands here automatically. It
   explains where to get a key (magnific.com → user menu → Organization
   Settings → API Keys → Create API key — requires a Business or Enterprise
   plan) and a form to paste it. The key is **verified against Magnific
   before it is saved** (`connect_magnific`): a bad paste is rejected
   immediately with Magnific's own reason, instead of failing silently the
   first time you try to generate an image. From here you can also manage
   the connection (disconnect) — open it any time via the left sidebar's
   "Manage providers" button, or the panel's `view="connect"` /
   `view="providers"` screens.
2. **Platform's generic Secrets panel** — still works. `magnific_api_key` is
   declared `write_mode="both"`, so the platform's own Secrets screen (`right`
   slot) can write it too, with no in-app validation.

A second secret, `magnific_webhook_secret`, is declared but **not read by any
handler yet** — see "Why polling, not webhooks" below.

### Managing / adding providers later

`providers.py` is the single seam for this: `list_provider_connections()`
returns one `ProviderConnection` per known backend, and the Providers screen
(`view="providers"`) renders whatever that list contains — today just
Magnific. Adding a second provider (e.g. Gemini) means adding one more
declared secret, one more `_xxx_connection()` builder, and one more entry in
`_KNOWN_PROVIDERS` — no panel redesign, no new slot.

## Workflow

1. **`create_media_brief`** — site, article title, summary, style direction,
   and how many inline images besides the featured one (0-8). Creates a
   `draft` package with one `pending` asset per role (`featured`, `inline_1`,
   `inline_2`, ...) and a role-appropriate prompt already built.
2. **`generate_media_package`** — generates every pending asset for a
   package. Runs in the background (`ctx.background_task`): you get an
   immediate acknowledgement, then a follow-up message when it's done. A
   package that's already generating refuses to start a second run.
3. **`get_media_package`** / **`list_media_packages`** — read one package in
   full, or list/filter by site and status.
4. **`regenerate_asset`** — redo exactly one asset (e.g. just the featured
   image), optionally with a prompt override, without touching the others.
5. **`update_asset_meta`** — edit an asset's alt text or caption without
   regenerating the image.
6. **`delete_media_package`** — permanently delete a package and its asset
   records (does **not** delete anything already hosted on Magnific's own
   servers — only the record inside Media Studio).

## Panels

- **left (`packages_nav`)** — package list, PLUS a permanent "Providers"
  status card at the top (connected / not connected) with a "Manage
  providers" button. This exists specifically to make the connection state
  and its entry point visible every time this panel renders, instead of
  requiring the user to already know where to look.
- **center, overlay (`studio`)** — ONE panel, four screens selected by a
  `view` parameter: `connect` (paste + validate the Magnific key), `providers`
  (status list, connect/disconnect, built to hold more than one provider),
  `editor` (brief form for a new package, or the full asset-grid editor for
  an existing one). A first-time user with no provider connected lands on
  `connect` by default.
- **right** — the platform's own generic Secrets panel. Still present
  (declared secrets always get one), and still usable directly, but no
  longer the only way in: `magnific_api_key` is `write_mode="both"`.

## Design notes

**Why polling, not `@ext.webhook`, in v1.** A confirmed platform bug in a
different extension (Asana Connector) means the webhook layer does not
proxy a handler's HTTP headers/status code back onto the wire, breaking
challenge/echo handshakes. Magnific's webhook model (HMAC-signed POST body)
is structurally different and might be unaffected, but that is unverified
end-to-end. Polling has zero dependency on that unverified path, so v1
polls; a webhook path can be added later once proven safe elsewhere.

**Why the provider response parsing is defensive.** Magnific's public docs
confirm the two endpoints (`POST /v1/ai/mystic`, `GET /v1/ai/mystic/{id}`)
and the `x-magnific-api-key` auth header, but a bot-protected/rendered docs
page did not yield a complete, guaranteed field-by-field response schema for
the task-status body. `magnific_client.py` tries several
documented-plausible keys and raises a structured `MEDIA_PROVIDER_ERROR`
(never a silent `None`) when a real response doesn't match any of them.

**Why `role` is a free string, not an enum.** A brief's slot vocabulary
(`featured`, `inline_1`, `inline_2`, ...) grows with `inline_count`; a
Pydantic enum would need a migration every time someone wants a fifth inline
slot. Validated in the handler against the roles the package actually has,
not against the type system.

**Why `provider` is on the package/asset even though only one exists.**
Deliberate: a second backend (e.g. Gemini) may plug in later, and keeping the
field now costs nothing but avoids a schema migration when it does.

## Known limitation

`magnific_webhook_secret` is declared as a secret but unused — no handler
reads it. If a webhook path is added later (once the platform's header-echo
bug is confirmed fixed or shown not to affect Magnific's HMAC model), this
secret is what it will verify signatures against.

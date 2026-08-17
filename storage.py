"""ctx.store persistence for media packages.

One collection, `packages`. IMPORTANT: `ctx.store.create()` mints its own
doc id server-side -- it does not accept a caller-supplied id (confirmed by
reading the real store client, not assumed). So `create_package` returns the
id the store handed back; callers must use THAT id afterwards, not one
generated up front. This differs from Asana/WordPress Hub's pattern of
scanning for an external provider id (site_id, gid) because we have no such
external id here -- the store's own id IS the package id.
"""

from __future__ import annotations

from datetime import datetime, timezone

PACKAGES_COLLECTION = "packages"
PROJECTS_COLLECTION = "projects"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def create_project(ctx, site_id: str, name: str = "") -> tuple[str, dict] | None:
    """Register a project (site) explicitly, idempotent on site_id.

    Returns None if a project with this site_id already exists -- callers
    surface that as a normal validation error, same pattern as
    create_media_brief's own input checks.
    """
    page = await ctx.store.query(PROJECTS_COLLECTION, limit=200)
    for doc in page.data:
        if doc.data.get("site_id") == site_id:
            return None
    payload = {
        "site_id": site_id, "name": name or site_id, "created_at": _now(),
        "about": "", "default_style_direction": "", "default_lang": "",
    }
    doc = await ctx.store.create(PROJECTS_COLLECTION, payload)
    return doc.id, dict(doc.data)


async def get_project(ctx, site_id: str) -> dict | None:
    """One project row by its site_id, or None if never explicitly
    registered (a brief-only "implicit" project, same case list_projects
    already handles by synthesizing a row -- this lookup just doesn't)."""
    page = await ctx.store.query(PROJECTS_COLLECTION, limit=200)
    for doc in page.data:
        if doc.data.get("site_id") == site_id:
            row = dict(doc.data)
            row["id"] = doc.id
            return row
    return None


async def update_project(ctx, site_id: str, patch: dict) -> dict | None:
    """Update a project's own fields (about/default_style_direction/
    default_lang/name). Auto-creates the project row first if it only
    existed implicitly (a brief referencing this site_id but no explicit
    create_project call yet) -- same as get_project's "implicit project"
    case, but this one needs a real doc id to write to."""
    page = await ctx.store.query(PROJECTS_COLLECTION, limit=200)
    existing_doc = next((doc for doc in page.data if doc.data.get("site_id") == site_id), None)
    if existing_doc is None:
        payload = {
            "site_id": site_id, "name": site_id, "created_at": _now(),
            "about": "", "default_style_direction": "", "default_lang": "",
        }
        created = await ctx.store.create(PROJECTS_COLLECTION, payload)
        doc_id, merged = created.id, dict(created.data)
    else:
        doc_id, merged = existing_doc.id, dict(existing_doc.data)
    merged.update(patch)
    await ctx.store.update(PROJECTS_COLLECTION, doc_id, merged)
    merged["id"] = doc_id
    return merged


async def list_projects(ctx) -> list[dict]:
    """Every project we work on: explicitly registered ones (via
    create_project / the sidebar's Add Project dialog) PLUS any `site`
    value already used by an existing media package that has no explicit
    project row yet -- so briefs created before a project was ever
    registered still show up as an "already existing" project, not as if
    they never happened.

    Each returned dict carries site_id, name, and brief_count (how many
    media packages currently reference that site).
    """
    projects_page = await ctx.store.query(PROJECTS_COLLECTION, limit=200)
    by_site_id: dict[str, dict] = {}
    for doc in projects_page.data:
        site_id = doc.data.get("site_id", "")
        if not site_id:
            continue
        by_site_id[site_id] = {
            "id": doc.id, "site_id": site_id,
            "name": doc.data.get("name") or site_id,
            "about": doc.data.get("about", ""),
            "default_style_direction": doc.data.get("default_style_direction", ""),
            "default_lang": doc.data.get("default_lang", ""),
            "created_at": doc.data.get("created_at", ""),
        }

    packages = await list_packages(ctx, limit=200)
    counts: dict[str, int] = {}
    for pkg in packages:
        site_id = pkg.get("site", "")
        if not site_id:
            continue
        counts[site_id] = counts.get(site_id, 0) + 1
        if site_id not in by_site_id:
            by_site_id[site_id] = {
                "id": "", "site_id": site_id, "name": site_id,
                "about": "", "default_style_direction": "", "default_lang": "",
                "created_at": "",
            }

    rows = list(by_site_id.values())
    for row in rows:
        row["brief_count"] = counts.get(row["site_id"], 0)
    rows.sort(key=lambda r: (r["created_at"] or "9999", r["site_id"]))
    return rows


async def create_package(ctx, data: dict) -> tuple[str, dict]:
    """Create a package doc. Returns (package_id, stored_data)."""
    payload = dict(data)
    payload["created_at"] = _now()
    payload["updated_at"] = payload["created_at"]
    doc = await ctx.store.create(PACKAGES_COLLECTION, payload)
    return doc.id, dict(doc.data)


async def get_package(ctx, package_id: str) -> dict | None:
    doc = await ctx.store.get(PACKAGES_COLLECTION, package_id)
    if doc is None:
        return None
    row = dict(doc.data)
    row["id"] = doc.id
    return row


async def update_package(ctx, package_id: str, patch: dict) -> dict | None:
    doc = await ctx.store.get(PACKAGES_COLLECTION, package_id)
    if doc is None:
        return None
    merged = dict(doc.data)
    merged.update(patch)
    merged["updated_at"] = _now()
    await ctx.store.update(PACKAGES_COLLECTION, package_id, merged)
    merged["id"] = package_id
    return merged


async def delete_package(ctx, package_id: str) -> bool:
    doc = await ctx.store.get(PACKAGES_COLLECTION, package_id)
    if doc is None:
        return False
    await ctx.store.delete(PACKAGES_COLLECTION, package_id)
    return True


async def list_packages(ctx, site: str = "", status: str = "", limit: int = 50) -> list[dict]:
    """Each returned dict carries its own store doc id under `id` so callers
    (handlers, panels) never need a second lookup to know the package id."""
    page = await ctx.store.query(PACKAGES_COLLECTION, limit=200)
    items = []
    for doc in page.data:
        row = dict(doc.data)
        row["id"] = doc.id
        items.append(row)
    if site:
        items = [p for p in items if p.get("site") == site]
    if status:
        items = [p for p in items if p.get("status") == status]
    items.sort(key=lambda p: p.get("updated_at", ""), reverse=True)
    return items[:limit]

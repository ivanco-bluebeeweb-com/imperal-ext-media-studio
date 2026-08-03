"""ctx.store persistence for media packages.

One collection, `packages`. IMPORTANT: `ctx.store.create()` mints its own
doc id server-side -- it does not accept a caller-supplied id (confirmed by
reading the real store client, not assumed). So `create_package` returns the
id the store handed back; callers must use THAT id afterwards, not one
generated up front. This differs from Asana/WP Site Connector's pattern of
scanning for an external provider id (site_id, gid) because we have no such
external id here -- the store's own id IS the package id.
"""

from __future__ import annotations

from datetime import datetime, timezone

PACKAGES_COLLECTION = "packages"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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

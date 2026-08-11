"""Per-tool prices, and the two ways a price list quietly goes wrong.

WHY THIS IS TESTED AT ALL. Prices are data, not logic, so the instinct is to
eyeball them once and move on. Both failure modes here are silent and only
surface as money:

  1. A NEW TOOL WITH NO PRICE. Classic Fast and the daily model-discovery
     tools (check_new_models, list_model_discovery_log) shipped in the same
     span as this test -- exactly the moment a price table quietly falls
     behind the manifest it is supposed to describe.

  2. GENERATION PRICED LIKE A SETTINGS SCREEN. generate_media_package is the
     only tool that spends the user's own Magnific quota on every single
     call (often several images per package). If it were priced like
     list_media_packages, the price table would say nothing true about cost.

Same discipline as Slack Connector's tests/test_pricing.py -- this repo's own
prior art for what a price-list test suite looks like.
"""

import json
import pathlib

MANIFEST = pathlib.Path(__file__).resolve().parent.parent / "imperal.json"


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _pricing() -> dict:
    pricing = _manifest().get("pricing")
    assert pricing, "manifest has no pricing block"
    return pricing


# --- every tool has a price --------------------------------------------------

def test_every_tool_has_a_price_and_no_price_is_orphaned():
    """THE INVARIANT THAT SURVIVES THE NEXT FEATURE.

    Checked in both directions. A missing price means a tool is billed by
    accident (or not at all) rather than by decision. An orphaned price means
    the table still mentions a tool that no longer exists.
    """
    tools = {t["name"] for t in _manifest()["tools"]}
    priced = set(_pricing()["tool_prices"])

    missing = tools - priced
    orphaned = priced - tools

    assert not missing, f"tools with no price: {sorted(missing)}"
    assert not orphaned, f"prices for tools that don't exist: {sorted(orphaned)}"


def test_no_price_is_negative_or_fractional():
    """Tokens are whole and non-negative. A negative price pays the user."""
    for name, price in _pricing()["tool_prices"].items():
        assert isinstance(price, int), f"{name}: price is not an integer ({price!r})"
        assert price >= 0, f"{name}: negative price ({price})"


# --- what free means ----------------------------------------------------------

def test_reading_and_settings_actions_are_free():
    """Looking at your own packages, providers, or the discovery log is not
    work, and must not be billed -- charging for list_providers would be
    charging someone to check whether they're even connected."""
    prices = _pricing()["tool_prices"]

    must_be_free = [
        "list_media_packages", "get_media_package", "list_providers",
        "check_new_models", "list_model_discovery_log",
    ]
    charged = {n: prices[n] for n in must_be_free if prices.get(n)}
    assert not charged, f"reads/settings must not cost tokens: {charged}"


def test_connecting_and_disconnecting_are_free():
    """Paying before the first success is paying for a key that might not
    even validate. Disconnecting removes a secret -- also not billable
    work."""
    prices = _pricing()["tool_prices"]
    assert prices["connect_magnific"] == 0
    assert prices["disconnect_magnific"] == 0


def test_the_free_list_agrees_with_the_prices():
    """`free_tools` is a convenience view; it must not disagree with the
    table -- a second list is a second truth, kept only because it is
    derived, and asserted so it cannot drift."""
    pricing = _pricing()
    derived = sorted(n for n, v in pricing["tool_prices"].items() if v == 0)
    assert pricing["free_tools"] == derived


# --- the shape of the scale, not just its presence ----------------------------

def test_generation_costs_more_than_every_other_paid_action():
    """generate_media_package is the only tool that spends the user's own
    Magnific quota on every call, often for several images at once. It must
    sit strictly above every other priced tool, or the price table stops
    telling the truth about what actually costs money."""
    prices = _pricing()["tool_prices"]
    other_paid = [v for n, v in prices.items() if n != "generate_media_package" and v > 0]
    assert prices["generate_media_package"] > max(other_paid), (
        "generate_media_package must be the single most expensive tool -- "
        "it is the only one that calls Magnific on the user's own quota"
    )


def test_regenerating_one_asset_costs_less_than_a_full_package():
    """A single-asset redo is real Magnific spend too, but strictly less
    work than generating a whole package from scratch."""
    prices = _pricing()["tool_prices"]
    assert 0 < prices["regenerate_asset"] < prices["generate_media_package"]


def test_deleting_costs_more_than_a_cheap_metadata_edit_but_nothing_external():
    """Deleting a package is irreversible locally (no Magnific call
    involved), so it should sit above a plain metadata edit but need not
    approach generation cost."""
    prices = _pricing()["tool_prices"]
    assert prices["delete_media_package"] > prices["update_asset_meta"]
    assert prices["delete_media_package"] < prices["generate_media_package"]


def test_the_scale_actually_separates_cheap_writes_from_real_generation():
    """A price list that is technically complete can still say nothing if
    every value collapses to 0/1. The scale must have real range, or it is a
    formality rather than a signal of what costs money."""
    prices = _pricing()["tool_prices"]
    assert len({v for v in prices.values()}) >= 4, (
        f"scale is too flat: levels are {sorted({v for v in prices.values()})}"
    )


def test_the_price_model_is_per_action_in_tokens():
    """Per-action is the point: a package with 5 inline images should cost
    visibly more in aggregate than a package with none, which only a
    per-action model (not a flat subscription) can show."""
    pricing = _pricing()
    assert pricing["model"] == "per_action"
    assert pricing["currency"] == "tokens"

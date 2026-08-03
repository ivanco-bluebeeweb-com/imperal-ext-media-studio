"""Small helpers shared by handlers and the provider client.

Kept separate from handlers.py for the same reason the Asana connector keeps
a `shared.py`: helpers used by more than one handler module should not live
inside either one, or the dependency direction becomes "write depends on
create" when the two are really peers.
"""

from __future__ import annotations

from imperal_sdk import ActionResult

# Magnific Mystic's documented `model` enum (docs.magnific.com/api-reference/
# mystic/post-mystic). Omitting the field entirely uses Mystic's own default
# -- that omission is exactly what v1 always did, so "" here must stay a
# legal, unvalidated choice for backward compatibility; only a NON-EMPTY
# value that isn't in this set is rejected.
MYSTIC_MODELS = ("realism", "fluid", "zen", "flexible", "super_real",
                  "editorial_portraits")


def is_valid_model(model: str) -> bool:
    return model == "" or model in MYSTIC_MODELS


def error(message: str, code: str, retryable: bool = False) -> ActionResult:
    """Error result carrying a mandatory structured code (see codes.py)."""
    return ActionResult.error(message, retryable, code=code)


def roles_for(inline_count: int) -> list[str]:
    """['featured', 'inline_1', 'inline_2', ...] for the given inline count."""
    return ["featured"] + [f"inline_{i}" for i in range(1, inline_count + 1)]


def prompt_for_role(role: str, article_title: str, summary: str, style_direction: str) -> str:
    """Build a Mystic prompt for one asset role.

    The featured image gets a wider, more "hero" framing instruction; inline
    images are told to be a supporting/detail shot so a package doesn't end
    up as N near-identical images.
    """
    base = summary.strip() or article_title.strip() or "a professional editorial photo"
    style = f" Style: {style_direction.strip()}." if style_direction.strip() else ""
    if role == "featured":
        framing = ("Wide hero shot suitable as a blog featured image, "
                   "clean composition, no embedded text or logos.")
    else:
        framing = ("A supporting detail shot illustrating a different aspect "
                   "of the same subject, no embedded text or logos.")
    return f"{base}. {framing}{style}".strip()


def default_alt_text(role: str, article_title: str) -> str:
    title = article_title.strip() or "article"
    if role == "featured":
        return f"Featured image for: {title}"
    return f"Supporting image for: {title}"

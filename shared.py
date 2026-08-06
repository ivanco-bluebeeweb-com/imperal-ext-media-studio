"""Small helpers shared by handlers and the provider client.

Kept separate from handlers.py for the same reason the Asana connector keeps
a `shared.py`: helpers used by more than one handler module should not live
inside either one, or the dependency direction becomes "write depends on
create" when the two are really peers.
"""

from __future__ import annotations

from imperal_sdk import ActionResult

import model_registry as mr

# Magnific Mystic's documented `model` enum (docs.magnific.com/api-reference/
# mystic/post-mystic). Omitting the field entirely uses Mystic's own default
# -- that omission is exactly what v1 always did, so "" here must stay a
# legal, unvalidated choice for backward compatibility; only a NON-EMPTY
# value that isn't in this set is rejected.
MYSTIC_MODELS = ("realism", "fluid", "zen", "flexible", "super_real",
                  "editorial_portraits")


def is_valid_model(model: str) -> bool:
    """Legacy check: empty (Mystic default) or one of Mystic's own 6 sub-
    styles. Kept exactly as-is for backward compatibility -- see
    `is_valid_model_choice` for the wider check that also accepts \"auto\"
    and other registered providers (Imagen 4, Gemini)."""
    return model == "" or model in MYSTIC_MODELS


def is_valid_model_choice(model: str) -> bool:
    """True for everything `model` is now allowed to be: empty (Mystic
    default), a Mystic sub-style, \"auto\" (automatic model selection --
    see model_registry.pick_model), or an id from the multi-provider
    registry (mystic/imagen4-fast/imagen4-ultra/gemini-2.5-flash)."""
    return is_valid_model(model) or model == "auto" or mr.is_known_model(model)


def valid_model_choices_hint() -> str:
    """Human-readable list of every legal `model` value, for error messages."""
    mystic = ", ".join(MYSTIC_MODELS)
    registry = ", ".join(m for m in mr.MODELS if m != "mystic")
    return (
        f"Mystic styles ({mystic}), a specific model ({registry}), "
        f"'auto' to let Media Hub pick automatically, or omit it for "
        f"Mystic's own default."
    )


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


def default_alt_text(role: str, article_title: str, lang: str = "") -> str:
    """Default alt text for one asset, phrased in the post's OWN language
    when a supported lang code is given -- alt text is user-facing content on
    the published page and must match the article's language, unlike the
    (always-English) image generation prompt. Falls back to English for an
    unrecognised/empty lang, same as v1 behaviour.
    """
    title = article_title.strip() or {
        "ru": "статья", "ro": "articol",
    }.get(lang, "article")
    templates = {
        "ru": {"featured": "Главное изображение к статье: {title}",
               "inline": "Иллюстрация к статье: {title}"},
        "ro": {"featured": "Imagine principală pentru articolul: {title}",
               "inline": "Imagine ilustrativă pentru articolul: {title}"},
    }
    key = "featured" if role == "featured" else "inline"
    lang_templates = templates.get(lang)
    if lang_templates:
        return lang_templates[key].format(title=title)
    if role == "featured":
        return f"Featured image for: {title}"
    return f"Supporting image for: {title}"


# Characters that mark a string as clearly NOT English: Cyrillic (Russian,
# etc.) and the Romanian-specific diacritics (ă â î ș ț). Image models like
# Magnific Mystic are documented and tuned for English prompts -- an article
# written in RU/RO must still get an English image prompt, so this is a hard
# gate at brief-creation time rather than a convention callers might forget.
def contains_non_english_text(*texts: str) -> str:
    """Return the offending snippet if any text has Cyrillic or Romanian
    diacritics, else "" (English/Latin-basic text is fine).
    """
    romanian_diacritics = set("ăâîșțĂÂÎȘȚ")
    for text in texts:
        if not text:
            continue
        for ch in text:
            if "\u0400" <= ch <= "\u04FF":  # Cyrillic range
                return text
            if ch in romanian_diacritics:
                return text
    return ""

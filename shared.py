"""Small helpers shared by handlers and the provider client.

Kept separate from handlers.py for the same reason the Asana connector keeps
a `shared.py`: helpers used by more than one handler module should not live
inside either one, or the dependency direction becomes "write depends on
create" when the two are really peers.
"""

from __future__ import annotations

import re
import time

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


def slugify(text: str, max_words: int = 8) -> str:
    """Turn free English text into a lowercase, hyphenated, ASCII slug --
    the same shape search engines and AEO/answer engines expect from a
    filename (e.g. 'heat-recovery-ventilator-for-apartments'), not the raw
    provider-generated name like 'result_IMAGEN4_ULTRA_f992763b...png' that
    upload_media was producing before this existed.

    Deliberately simple (word-splitting + a denylist regex) rather than a
    full Unicode transliteration table: `text` here is always the brief's
    English `article_title` (gated non-English at brief-creation time), so
    there is no Cyrillic/Romanian text to transliterate in the first place.
    """
    words = re.findall(r"[a-z0-9]+", text.lower())
    return "-".join(words[:max_words]) or "image"


def filename_for_asset(article_title: str, role: str) -> str:
    """SEO/AEO-optimized base filename (no extension) for one asset --
    the topic slug plus a role suffix so featured/inline_1/inline_2 never
    collide, e.g. 'heat-recovery-ventilator-for-apartments-featured'.

    This is what actually lands as the file name once the image reaches a
    site (WordPress Hub's upload_media/create_post carries it through to
    the Imperal Bridge, which now honours an explicit filename instead of
    deriving one from the provider's own generated URL).
    """
    base = slugify(article_title)
    suffix = "featured" if role == "featured" else role.replace("_", "-")
    return f"{base}-{suffix}"


# Text-on-image policy: whether the generated image itself is ALLOWED to
# render legible text (labels, numbers, short captions baked into the
# picture) or must stay text-free. Content Strategy Hub decides this per
# brief (e.g. a price/comparison article benefits from a labelled diagram;
# a generic hero shot should not have random illegible text) and it flows
# through create_media_brief/build_media_brief_handoff -- see
# `text_policy_clause` below for how it changes the actual prompt.
TEXT_POLICY_NO_TEXT = "no_text"
TEXT_POLICY_ALLOW_TEXT = "allow_text"
VALID_TEXT_POLICIES = (TEXT_POLICY_NO_TEXT, TEXT_POLICY_ALLOW_TEXT)


def is_valid_model_choice(model: str) -> bool:
    """True for everything `model` is now allowed to be: empty (Mystic
    default), a Mystic sub-style, \"auto\" (automatic model selection --
    see model_registry.pick_model), or a SELECTABLE id from the
    multi-provider registry (mystic/gemini-2.5-flash/nano-banana-pro/...).

    Google Imagen 4 Ultra/Fast are deliberately EXCLUDED here even though
    their rows still exist in `mr.MODELS` -- standing user directive, see
    model_registry.DISABLED_MODEL_IDS's docstring. Using
    `mr.is_selectable_model` instead of `mr.is_known_model` is what makes an
    explicit `model=\"imagen4-ultra\"` (or -fast) request fail with
    MEDIA_INVALID_MODEL exactly like any other unregistered id, for every
    caller (create_media_brief, regenerate_asset)."""
    return is_valid_model(model) or model == "auto" or mr.is_selectable_model(model)


def valid_model_choices_hint() -> str:
    """Human-readable list of every legal `model` value, for error messages.

    Disabled ids (see model_registry.DISABLED_MODEL_IDS) are deliberately
    left out -- they must not be suggested as a valid choice either."""
    mystic = ", ".join(MYSTIC_MODELS)
    registry = ", ".join(
        m for m in mr.MODELS if m != "mystic" and m not in mr.DISABLED_MODEL_IDS
    )
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


# Every blogpost image generated by this pipeline must be 4:3 landscape --
# the user's explicit standing directive. `classic_4_3` is the exact enum
# value confirmed on BOTH Mystic and Imagen 4's documented request bodies
# (docs.magnific.com/api-reference/mystic/post-mystic and
# .../text-to-image/imagen4-fast/generate both list the identical
# aspect_ratio enum, `classic_4_3` = "4:3 aspect ratio, horizontal/landscape
# orientation"). Kept as one shared constant so every body-builder that
# supports the field uses the exact same value -- no per-model drift.
ASPECT_RATIO_4_3 = "classic_4_3"

# Human-readable names for the post's own language, used inside the
# generated prompt's language clause below -- kept minimal (ru/ro, the two
# languages this pipeline currently writes articles in) rather than a full
# ISO-639 table, since contains_non_english_text only ever gates on these
# two scripts today.
_LANG_NAMES = {"ru": "Russian", "ro": "Romanian"}


def text_policy_clause(role: str, text_policy: str, image_text: str = "") -> str:
    """Framing clause for whether THIS asset may render legible in-image
    text (labels, numbers, short captions baked into the picture) or must
    stay text-free -- driven by Content Strategy Hub's per-brief decision
    (see `TEXT_POLICY_NO_TEXT`/`TEXT_POLICY_ALLOW_TEXT` above).

    This is deliberately binary and explicit, never a vague maybe: either
    the image is text-free, or the prompt states the EXACT words the model
    must render. A prompt that merely permitted "a label if it fits" gave
    an image model nothing concrete to draw, so it either invented
    meaningless text or drew nothing -- neither is a real answer to
    "with what text". `image_text` is the caller's own supplied wording
    (e.g. a brief's CTA phrase or a price); TEXT_POLICY_ALLOW_TEXT without
    it falls back to TEXT_POLICY_NO_TEXT's clause instead of guessing.
    """
    if text_policy == TEXT_POLICY_ALLOW_TEXT and image_text.strip():
        return (f'Render this exact short text legibly within the image, '
                 f'naturally integrated into the scene (e.g. as a label, '
                 f'sign, screen, or caption): "{image_text.strip()}". No '
                 f'other text or logos.')
    return "clean composition, no embedded text or logos."


def prompt_for_role(
    role: str, article_title: str, summary: str, style_direction: str, lang: str = "",
    text_policy: str = TEXT_POLICY_NO_TEXT, image_text: str = "",
) -> str:
    """Build an image-generation prompt for one asset role.

    The featured image gets a wider, more "hero" framing instruction; inline
    images are told to be a supporting/detail shot so a package doesn't end
    up as N near-identical images. The prompt text itself always stays
    English (Mystic/Imagen4/Gemini are documented and tuned for English
    input -- see contains_non_english_text's gate at brief-creation time).

    `lang` is the ARTICLE's own language (e.g. 'ru', 'ro'), not the prompt's
    language. When given, an explicit clause is appended instructing that
    ANY text rendered inside the image itself (signage, labels, screens...)
    must be written in that language, matching the published article --
    the user's standing directive. When style_direction already says
    "no embedded text" this clause is harmless (there's no text to render),
    so it's always safe to add rather than conditional on style_direction.

    `text_policy` (TEXT_POLICY_NO_TEXT default, or TEXT_POLICY_ALLOW_TEXT)
    decides whether the framing clause forbids or permits legible in-image
    text -- see `text_policy_clause`. When TEXT_POLICY_ALLOW_TEXT is used,
    `image_text` must carry the actual words to render; the clause always
    states either "no text" or the exact text, never a vague "maybe".
    """
    base = summary.strip() or article_title.strip() or "a professional editorial photo"
    style = f" Style: {style_direction.strip()}." if style_direction.strip() else ""
    text_clause = text_policy_clause(role, text_policy, image_text)
    if role == "featured":
        framing = f"Wide hero shot suitable as a blog featured image, {text_clause}"
    else:
        framing = (f"A supporting detail shot illustrating a different aspect "
                   f"of the same subject, {text_clause}")
    lang_name = _LANG_NAMES.get(lang)
    lang_clause = (
        f" If any text, signage, labels or captions appear within the "
        f"image, they must be written in {lang_name} (the article's own "
        f"language) -- never in English or any other language."
        if lang_name else ""
    )
    return f"{base}. {framing}{style}{lang_clause}".strip()


def is_absolute_public_url(url: str) -> bool:
    """True only for a well-formed absolute http(s) URL with a real host.

    BUG THIS GUARDS AGAINST (found 2026-08-13, live g4s.md draft): storage
    upload can return a value that is non-empty but NOT a usable link (e.g.
    a bare relative storage path like ``media-studio/<id>/featured/original.png``
    instead of ``https://.../media-studio/...``). Callers that only checked
    ``uploaded.url or fallback_url`` treated ANY non-empty string as success,
    so a relative path silently replaced a perfectly good provider URL and
    was later rejected by WordPress Bridge with \"source_url must be a
    well-formed URL\" -- the picture never attached, with no visible error
    at generation time. This check requires an actual scheme + host, so a
    relative/malformed value is correctly treated as \"no URL\" and callers
    fall back to `fallback_url` instead of persisting garbage.
    """
    if not url:
        return False
    parsed = re.match(r"^(https?)://([^/\s]+)", url.strip())
    return bool(parsed)


def is_image_url_expired(url: str, *, now: float | None = None) -> bool:
    """True if a Magnific/Freepik CDN image URL's signed token has already
    expired (or will expire within the next 60s -- a small safety margin).

    CONFIRMED URL SHAPE (read live off a real broken package,
    package_id=42980b3b-8995-4c4f-94ef-3c120970ff2f): CDN URLs look like
    ``https://cdn-magnific.freepik.com/....png?token=exp=<epoch>~hmac=...&size=stable``
    -- the `token` query param's VALUE is itself a `~`-joined mini-format
    (`exp=<unix_ts>~hmac=<sig>`), not a normal query string. This is exactly
    why a `status=ready` row stored permanently in our own DB can show
    "Image unavailable" forever once the token's `exp` timestamp passes --
    the DB status is not time-bound, the CDN link is.

    Returns False (treat as still valid) for any URL that doesn't match this
    exact shape -- e.g. a future provider/CDN with no expiring token -- so
    this is a targeted fix for the confirmed shape, not a blanket assumption
    that every image URL expires.

    `now` is opt-in and used only by tests; real callers never pass it and
    get the live clock, exactly as before.
    """
    if not url:
        return False
    match = re.search(r"token=exp%3D(\d+)|token=exp=(\d+)", url)
    if not match:
        return False
    exp_str = match.group(1) or match.group(2)
    try:
        exp_ts = int(exp_str)
    except ValueError:
        return False
    clock = now if now is not None else time.time()
    return exp_ts <= clock + 60


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

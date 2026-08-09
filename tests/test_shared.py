import shared


def test_roles_for_zero_inline():
    assert shared.roles_for(0) == ["featured"]


def test_roles_for_multiple_inline():
    assert shared.roles_for(3) == ["featured", "inline_1", "inline_2", "inline_3"]


def test_prompt_for_role_featured_uses_hero_framing():
    prompt = shared.prompt_for_role("featured", "Article Title", "A summary", "")
    assert "a summary" in prompt.lower()
    assert "hero" in prompt.lower()


def test_prompt_for_role_inline_uses_detail_framing():
    prompt = shared.prompt_for_role("inline_1", "Title", "Summary text", "")
    assert "detail" in prompt.lower()


def test_prompt_for_role_includes_style_direction_when_given():
    prompt = shared.prompt_for_role("featured", "Title", "Summary", "industrial, blue palette")
    assert "industrial" in prompt


def test_prompt_for_role_falls_back_to_title_when_no_summary():
    prompt = shared.prompt_for_role("featured", "My Title", "", "")
    assert "My Title" in prompt


def test_prompt_for_role_no_lang_has_no_language_clause():
    prompt = shared.prompt_for_role("featured", "Title", "Summary", "")
    assert "language" not in prompt.lower()


def test_prompt_for_role_russian_adds_language_clause_for_any_embedded_text():
    prompt = shared.prompt_for_role("featured", "Title", "Summary", "", "ru")
    assert "Russian" in prompt


def test_prompt_for_role_romanian_adds_language_clause_for_any_embedded_text():
    prompt = shared.prompt_for_role("inline_1", "Title", "Summary", "", "ro")
    assert "Romanian" in prompt


def test_prompt_for_role_unknown_lang_adds_no_clause():
    prompt = shared.prompt_for_role("featured", "Title", "Summary", "", "de")
    assert "language" not in prompt.lower()


def test_default_alt_text_featured_vs_inline():
    featured = shared.default_alt_text("featured", "My Article")
    inline = shared.default_alt_text("inline_1", "My Article")
    assert "My Article" in featured
    assert "My Article" in inline
    assert featured != inline


def test_default_alt_text_defaults_to_english_when_lang_omitted():
    text = shared.default_alt_text("featured", "My Article")
    assert text == "Featured image for: My Article"


def test_default_alt_text_russian():
    featured = shared.default_alt_text("featured", "Как выбрать вентиляцию", "ru")
    inline = shared.default_alt_text("inline_1", "Как выбрать вентиляцию", "ru")
    assert "Как выбрать вентиляцию" in featured
    assert "Главное изображение" in featured
    assert "Иллюстрация" in inline
    assert featured != inline


def test_default_alt_text_romanian():
    featured = shared.default_alt_text("featured", "Cum alegi ventilația", "ro")
    inline = shared.default_alt_text("inline_2", "Cum alegi ventilația", "ro")
    assert "Cum alegi ventilația" in featured
    assert "Imagine principală" in featured
    assert "Imagine ilustrativă" in inline


def test_default_alt_text_unknown_lang_falls_back_to_english():
    text = shared.default_alt_text("featured", "My Article", "de")
    assert text == "Featured image for: My Article"


def test_default_alt_text_blank_title_uses_lang_specific_placeholder():
    assert shared.default_alt_text("featured", "", "ru") == "Главное изображение к статье: статья"
    assert shared.default_alt_text("featured", "", "ro") == "Imagine principală pentru articolul: articol"
    assert shared.default_alt_text("featured", "") == "Featured image for: article"


def test_error_carries_structured_code():
    result = shared.error("boom", "MEDIA_PROVIDER_ERROR")
    assert result.status == "error"
    assert result.error == "boom"
    assert result.error_code == "MEDIA_PROVIDER_ERROR"


# --------------------------- is_image_url_expired ---------------------------

def test_is_image_url_expired_empty_url_is_not_expired():
    assert not shared.is_image_url_expired("")


def test_is_image_url_expired_url_without_token_is_not_expired():
    assert not shared.is_image_url_expired("https://cdn.example/img.png")


def test_is_image_url_expired_past_exp_is_expired():
    url = "https://cdn.freepik.com/img.png?token=exp=1000~hmac=abc&size=stable"
    assert shared.is_image_url_expired(url, now=2000)


def test_is_image_url_expired_future_exp_is_not_expired():
    url = "https://cdn.freepik.com/img.png?token=exp=5000~hmac=abc&size=stable"
    assert not shared.is_image_url_expired(url, now=2000)


def test_is_image_url_expired_exactly_at_deadline_is_expired():
    url = "https://cdn.freepik.com/img.png?token=exp=2000~hmac=abc&size=stable"
    assert shared.is_image_url_expired(url, now=2000)


# --------------------------- ASPECT_RATIO_4_3 ---------------------------

def test_aspect_ratio_4_3_is_the_documented_classic_4_3_enum_value():
    assert shared.ASPECT_RATIO_4_3 == "classic_4_3"


# --------------------------- slugify / filename_for_asset ---------------------------

def test_slugify_lowercases_and_hyphenates():
    assert shared.slugify("Heat Recovery Ventilator") == "heat-recovery-ventilator"


def test_slugify_strips_punctuation():
    assert shared.slugify("Boilers 101: A Guide!") == "boilers-101-a-guide"


def test_slugify_caps_word_count():
    long_title = "one two three four five six seven eight nine ten"
    assert shared.slugify(long_title) == "one-two-three-four-five-six-seven-eight"


def test_slugify_empty_text_falls_back_to_image():
    assert shared.slugify("") == "image"


def test_filename_for_asset_featured_role():
    assert shared.filename_for_asset("Heat Recovery Ventilator", "featured") == \
        "heat-recovery-ventilator-featured"


def test_filename_for_asset_inline_role_uses_hyphenated_suffix():
    assert shared.filename_for_asset("Heat Recovery Ventilator", "inline_1") == \
        "heat-recovery-ventilator-inline-1"


def test_filename_for_asset_two_inline_roles_never_collide():
    a = shared.filename_for_asset("Boilers 101", "inline_1")
    b = shared.filename_for_asset("Boilers 101", "inline_2")
    assert a != b


# --------------------------- text_policy_clause ---------------------------

def test_text_policy_clause_default_forbids_text():
    clause = shared.text_policy_clause("featured", shared.TEXT_POLICY_NO_TEXT)
    assert "no embedded text" in clause


def test_text_policy_clause_allow_text_permits_legible_text():
    clause = shared.text_policy_clause("featured", shared.TEXT_POLICY_ALLOW_TEXT)
    assert "no embedded text" not in clause
    assert "legible" in clause.lower() or "text" in clause.lower()


def test_prompt_for_role_default_is_text_free():
    prompt = shared.prompt_for_role("featured", "Boilers 101", "A guide", "")
    assert "no embedded text" in prompt


def test_prompt_for_role_allow_text_drops_the_no_text_clause():
    prompt = shared.prompt_for_role(
        "featured", "Price comparison", "A guide", "", text_policy=shared.TEXT_POLICY_ALLOW_TEXT,
    )
    assert "no embedded text" not in prompt

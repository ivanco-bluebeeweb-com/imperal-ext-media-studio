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


def test_default_alt_text_featured_vs_inline():
    featured = shared.default_alt_text("featured", "My Article")
    inline = shared.default_alt_text("inline_1", "My Article")
    assert "My Article" in featured
    assert "My Article" in inline
    assert featured != inline


def test_error_carries_structured_code():
    result = shared.error("boom", "MEDIA_PROVIDER_ERROR")
    assert result.status == "error"
    assert result.error == "boom"
    assert result.error_code == "MEDIA_PROVIDER_ERROR"

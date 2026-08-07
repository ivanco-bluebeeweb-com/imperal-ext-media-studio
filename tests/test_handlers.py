import pytest

import codes as c
import handlers as h
from models import (
    CreateMediaBriefParams,
    DeleteMediaPackageParams,
    GenerateMediaPackageParams,
    GetMediaPackageParams,
    ListMediaPackagesParams,
    RegenerateAssetParams,
    UpdateAssetMetaParams,
)


@pytest.mark.asyncio
async def test_create_media_brief_empty_is_rejected(ctx):
    result = await h.create_media_brief(
        ctx, CreateMediaBriefParams(site="g4s.md", article_title="", summary="", inline_count=1),
    )
    assert result.status == "error"
    assert result.error_code == c.MEDIA_EMPTY_BRIEF


@pytest.mark.asyncio
async def test_create_media_brief_rejects_russian_prompt(ctx):
    """Article can be RU/RO -- but the image brief itself must be English,
    since Magnific Mystic is documented and tuned for English prompts."""
    result = await h.create_media_brief(
        ctx, CreateMediaBriefParams(
            site="climtec.md", article_title="Рекуператор тепла в Молдове",
            summary="Как выбрать систему", inline_count=1,
        ),
    )
    assert result.status == "error"
    assert result.error_code == c.MEDIA_PROMPT_NOT_ENGLISH


@pytest.mark.asyncio
async def test_create_media_brief_rejects_romanian_diacritics(ctx):
    result = await h.create_media_brief(
        ctx, CreateMediaBriefParams(
            site="climtec.md", article_title="Recuperator de căldură", inline_count=0,
        ),
    )
    assert result.status == "error"
    assert result.error_code == c.MEDIA_PROMPT_NOT_ENGLISH


@pytest.mark.asyncio
async def test_create_media_brief_accepts_english(ctx):
    result = await h.create_media_brief(
        ctx, CreateMediaBriefParams(
            site="climtec.md", article_title="Heat recovery unit in Moldova",
            summary="How to choose the right system", inline_count=1,
        ),
    )
    assert result.status == "success"


@pytest.mark.asyncio
async def test_create_media_brief_builds_featured_plus_inline_assets(ctx):
    result = await h.create_media_brief(
        ctx, CreateMediaBriefParams(
            site="g4s.md", article_title="Boilers 101", summary="A guide", inline_count=2,
        ),
    )
    assert result.status == "success"
    assert [a.role for a in result.data.assets] == ["featured", "inline_1", "inline_2"]
    assert result.data.status == "draft"
    assert all(a.status == "pending" for a in result.data.assets)


@pytest.mark.asyncio
async def test_generate_media_package_missing_package(ctx):
    result = await h.generate_media_package(ctx, GenerateMediaPackageParams(package_id="nope"))
    assert result.status == "error"
    assert result.error_code == c.MEDIA_PACKAGE_NOT_FOUND


@pytest.mark.asyncio
async def test_generate_media_package_without_key_configured(ctx):
    brief = await h.create_media_brief(
        ctx, CreateMediaBriefParams(article_title="T", summary="S", inline_count=0),
    )
    result = await h.generate_media_package(
        ctx, GenerateMediaPackageParams(package_id=brief.data.id),
    )
    assert result.status == "error"
    assert result.error_code == c.MEDIA_KEY_NOT_CONFIGURED


@pytest.mark.asyncio
async def test_generate_media_package_already_generating(ctx_with_key):
    brief = await h.create_media_brief(
        ctx_with_key, CreateMediaBriefParams(article_title="T", summary="S", inline_count=0),
    )
    import storage as st
    await st.update_package(ctx_with_key, brief.data.id, {"status": "generating"})
    result = await h.generate_media_package(
        ctx_with_key, GenerateMediaPackageParams(package_id=brief.data.id),
    )
    assert result.status == "error"
    assert result.error_code == c.MEDIA_ALREADY_GENERATING


@pytest.mark.asyncio
async def test_generate_media_package_runs_synchronously_when_spawn_fails(ctx_with_key, monkeypatch):
    """If ctx.background_task itself can't spawn (no kernel spawn hook --
    seen both from a terminal coding session AND from a real panel button
    click on a published app), generation must NOT be aborted with a
    'try again from the panel' error the user can't act on. Instead the same
    work() coroutine runs synchronously in this same call and the images are
    actually produced -- mirroring SEO Audit Engine's audit_sites fallback.
    """
    brief = await h.create_media_brief(
        ctx_with_key, CreateMediaBriefParams(article_title="T", summary="S", inline_count=0),
    )

    async def fake_generate_image(ctx, api_key, prompt, **kwargs):
        return "https://cdn.example/img.png"
    monkeypatch.setattr(h.mc, "generate_image", fake_generate_image)

    async def broken_background_task(coro, *, long_running=False, name=""):
        raise RuntimeError(
            "ctx.background_task not available in this context — the "
            "Context was constructed without a kernel-injected spawn hook."
        )

    ctx_with_key.background_task = broken_background_task

    result = await h.generate_media_package(
        ctx_with_key, GenerateMediaPackageParams(package_id=brief.data.id),
    )
    assert result.status == "success"
    assert result.data.status == "ready"
    assert result.data.assets[0].image_url == "https://cdn.example/img.png"

    import storage as st
    row = await st.get_package(ctx_with_key, brief.data.id)
    assert row["status"] == "ready"


@pytest.mark.asyncio
async def test_generate_media_package_propagates_other_spawn_errors(ctx_with_key):
    """Only RuntimeError/AttributeError from background_task itself mean
    \"no spawn hook\" -- any other exception type must NOT be silently
    swallowed into a synchronous fallback, since that could mask a real bug
    in the spawn plumbing.
    """
    brief = await h.create_media_brief(
        ctx_with_key, CreateMediaBriefParams(article_title="T", summary="S", inline_count=0),
    )

    async def weird_background_task(coro, *, long_running=False, name=""):
        coro.close()
        raise ValueError("something unrelated broke")

    ctx_with_key.background_task = weird_background_task

    with pytest.raises(ValueError):
        await h.generate_media_package(
            ctx_with_key, GenerateMediaPackageParams(package_id=brief.data.id),
        )


@pytest.mark.asyncio
async def test_regenerate_asset_runs_synchronously_when_spawn_fails(ctx_with_key, monkeypatch):
    brief = await h.create_media_brief(
        ctx_with_key, CreateMediaBriefParams(article_title="T", summary="S", inline_count=0),
    )

    async def fake_generate_image(ctx, api_key, prompt, **kwargs):
        return "https://cdn.example/img2.png"
    monkeypatch.setattr(h.mc, "generate_image", fake_generate_image)

    async def broken_background_task(coro, *, long_running=False, name=""):
        raise RuntimeError("ctx.background_task not available in this context")

    ctx_with_key.background_task = broken_background_task

    result = await h.regenerate_asset(
        ctx_with_key, RegenerateAssetParams(package_id=brief.data.id, role="featured"),
    )
    assert result.status == "success"
    assert result.data.status == "ready"
    assert result.data.image_url == "https://cdn.example/img2.png"

    import storage as st
    row = await st.get_package(ctx_with_key, brief.data.id)
    featured = next(a for a in row["assets"] if a["role"] == "featured")
    assert featured["status"] == "ready"


@pytest.mark.asyncio
async def test_generate_media_package_success_end_to_end(ctx_with_key, monkeypatch):
    brief = await h.create_media_brief(
        ctx_with_key, CreateMediaBriefParams(article_title="T", summary="S", inline_count=1),
    )

    async def fake_generate_image(ctx, api_key, prompt, **kwargs):
        return "https://cdn.example/img.png"

    monkeypatch.setattr(h.mc, "generate_image", fake_generate_image)

    ack = await h.generate_media_package(
        ctx_with_key, GenerateMediaPackageParams(package_id=brief.data.id),
    )
    assert ack.status == "success"
    assert ack.data.status == "generating"

    final = ctx_with_key.last_background_result
    assert final.status == "success"
    assert final.data.status == "ready"
    assert all(a.status == "ready" and a.image_url for a in final.data.assets)
    assert all(a.alt_text for a in final.data.assets)


@pytest.mark.asyncio
async def test_generate_media_package_uses_native_title_and_lang_for_alt_text(ctx_with_key, monkeypatch):
    brief = await h.create_media_brief(
        ctx_with_key, CreateMediaBriefParams(
            article_title="How to choose a ventilation system", summary="S", inline_count=1,
            lang="ru", native_title="Как выбрать систему вентиляции",
        ),
    )
    assert brief.data.lang == "ru"
    assert brief.data.native_title == "Как выбрать систему вентиляции"

    async def fake_generate_image(ctx, api_key, prompt, **kwargs):
        assert "Как выбрать" not in prompt  # image PROMPT stays English regardless of lang
        return "https://cdn.example/img.png"

    monkeypatch.setattr(h.mc, "generate_image", fake_generate_image)

    await h.generate_media_package(
        ctx_with_key, GenerateMediaPackageParams(package_id=brief.data.id),
    )
    final = ctx_with_key.last_background_result
    assert final.status == "success"
    featured = next(a for a in final.data.assets if a.role == "featured")
    assert "Как выбрать систему вентиляции" in featured.alt_text
    assert featured.caption and "Как выбрать систему вентиляции" in featured.caption


@pytest.mark.asyncio
async def test_generate_media_package_all_failed_reports_error(ctx_with_key, monkeypatch):
    brief = await h.create_media_brief(
        ctx_with_key, CreateMediaBriefParams(article_title="T", summary="S", inline_count=0),
    )

    async def failing_generate_image(ctx, api_key, prompt, **kwargs):
        raise h.mc.ProviderError("boom", "MEDIA_PROVIDER_ERROR")

    monkeypatch.setattr(h.mc, "generate_image", failing_generate_image)

    await h.generate_media_package(ctx_with_key, GenerateMediaPackageParams(package_id=brief.data.id))
    final = ctx_with_key.last_background_result
    assert final.status == "error"
    assert final.error_code == c.MEDIA_PROVIDER_ERROR


@pytest.mark.asyncio
async def test_list_media_packages_filters(ctx):
    await h.create_media_brief(ctx, CreateMediaBriefParams(site="a.md", article_title="A", inline_count=0))
    await h.create_media_brief(ctx, CreateMediaBriefParams(site="b.md", article_title="B", inline_count=0))
    result = await h.list_media_packages(ctx, ListMediaPackagesParams(site="a.md"))
    assert len(result.data.items) == 1
    assert result.data.items[0].site == "a.md"


@pytest.mark.asyncio
async def test_get_media_package_not_found(ctx):
    result = await h.get_media_package(ctx, GetMediaPackageParams(package_id="nope"))
    assert result.status == "error"
    assert result.error_code == c.MEDIA_PACKAGE_NOT_FOUND


@pytest.mark.asyncio
async def test_get_media_package_found(ctx):
    brief = await h.create_media_brief(ctx, CreateMediaBriefParams(article_title="T", inline_count=0))
    result = await h.get_media_package(ctx, GetMediaPackageParams(package_id=brief.data.id))
    assert result.status == "success"
    assert result.data.id == brief.data.id


@pytest.mark.asyncio
async def test_regenerate_asset_unknown_role(ctx_with_key):
    brief = await h.create_media_brief(ctx_with_key, CreateMediaBriefParams(article_title="T", inline_count=0))
    result = await h.regenerate_asset(
        ctx_with_key, RegenerateAssetParams(package_id=brief.data.id, role="inline_9"),
    )
    assert result.status == "error"
    assert result.error_code == c.MEDIA_ASSET_NOT_FOUND


@pytest.mark.asyncio
async def test_regenerate_asset_success(ctx_with_key, monkeypatch):
    brief = await h.create_media_brief(ctx_with_key, CreateMediaBriefParams(article_title="T", inline_count=0))

    async def fake_generate_image(ctx, api_key, prompt, **kwargs):
        return "https://cdn.example/regen.png"

    monkeypatch.setattr(h.mc, "generate_image", fake_generate_image)

    await h.regenerate_asset(
        ctx_with_key, RegenerateAssetParams(package_id=brief.data.id, role="featured"),
    )
    final = ctx_with_key.last_background_result
    assert final.status == "success"
    assert final.data.image_url == "https://cdn.example/regen.png"


@pytest.mark.asyncio
async def test_create_media_brief_invalid_model_rejected(ctx):
    result = await h.create_media_brief(
        ctx, CreateMediaBriefParams(article_title="T", summary="S", inline_count=0,
                                     model="not-a-real-model"),
    )
    assert result.status == "error"
    assert result.error_code == c.MEDIA_INVALID_MODEL


@pytest.mark.asyncio
async def test_create_media_brief_omitted_model_uses_third_party_auto_policy(ctx):
    """No explicit model is now intentionally third-party-first, not Mystic."""
    result = await h.create_media_brief(
        ctx, CreateMediaBriefParams(article_title="T", summary="S", inline_count=1),
    )
    assert result.status == "success"
    assert result.data.model == "auto"
    assert all(a.model in {"imagen4-ultra", "imagen4-fast", "gemini-2.5-flash"}
               for a in result.data.assets)


@pytest.mark.asyncio
async def test_create_media_brief_valid_model_is_stored_on_every_asset(ctx):
    result = await h.create_media_brief(
        ctx, CreateMediaBriefParams(article_title="T", summary="S", inline_count=1,
                                     model="fluid"),
    )
    assert result.status == "success"
    assert result.data.model == "fluid"
    assert all(a.model == "fluid" for a in result.data.assets)


@pytest.mark.asyncio
async def test_generate_media_package_forwards_model_to_provider(ctx_with_key, monkeypatch):
    brief = await h.create_media_brief(
        ctx_with_key, CreateMediaBriefParams(article_title="T", summary="S", inline_count=0,
                                              model="super_real"),
    )
    seen_models = []

    async def fake_generate_image(ctx, api_key, prompt, **kwargs):
        seen_models.append(kwargs.get("model"))
        return "https://cdn.example/img.png"

    monkeypatch.setattr(h.mc, "generate_image", fake_generate_image)
    await h.generate_media_package(
        ctx_with_key, GenerateMediaPackageParams(package_id=brief.data.id),
    )
    assert seen_models == ["super_real"]


@pytest.mark.asyncio
async def test_regenerate_asset_invalid_model_override_rejected(ctx_with_key):
    brief = await h.create_media_brief(ctx_with_key, CreateMediaBriefParams(article_title="T", inline_count=0))
    result = await h.regenerate_asset(
        ctx_with_key, RegenerateAssetParams(package_id=brief.data.id, role="featured",
                                             model="nope"),
    )
    assert result.status == "error"
    assert result.error_code == c.MEDIA_INVALID_MODEL


@pytest.mark.asyncio
async def test_regenerate_asset_model_override_forwarded(ctx_with_key, monkeypatch):
    brief = await h.create_media_brief(
        ctx_with_key, CreateMediaBriefParams(article_title="T", inline_count=0, model="realism"),
    )
    seen_models = []

    async def fake_generate_image(ctx, api_key, prompt, **kwargs):
        seen_models.append(kwargs.get("model"))
        return "https://cdn.example/regen.png"

    monkeypatch.setattr(h.mc, "generate_image", fake_generate_image)
    await h.regenerate_asset(
        ctx_with_key, RegenerateAssetParams(package_id=brief.data.id, role="featured",
                                             model="zen"),
    )
    assert seen_models == ["zen"]


@pytest.mark.asyncio
async def test_update_asset_meta_edits_alt_and_caption(ctx):
    brief = await h.create_media_brief(ctx, CreateMediaBriefParams(article_title="T", inline_count=0))
    result = await h.update_asset_meta(ctx, UpdateAssetMetaParams(
        package_id=brief.data.id, role="featured", alt_text="new alt", caption="new caption",
    ))
    assert result.status == "success"
    assert result.data.alt_text == "new alt"
    assert result.data.caption == "new caption"


@pytest.mark.asyncio
async def test_update_asset_meta_missing_package(ctx):
    result = await h.update_asset_meta(ctx, UpdateAssetMetaParams(
        package_id="nope", role="featured", alt_text="x", caption="",
    ))
    assert result.status == "error"
    assert result.error_code == c.MEDIA_PACKAGE_NOT_FOUND


@pytest.mark.asyncio
async def test_delete_media_package_success_then_missing(ctx):
    brief = await h.create_media_brief(ctx, CreateMediaBriefParams(article_title="T", inline_count=0))
    result = await h.delete_media_package(ctx, DeleteMediaPackageParams(package_id=brief.data.id))
    assert result.status == "success"
    again = await h.delete_media_package(ctx, DeleteMediaPackageParams(package_id=brief.data.id))
    assert again.status == "error"
    assert again.error_code == c.MEDIA_PACKAGE_NOT_FOUND

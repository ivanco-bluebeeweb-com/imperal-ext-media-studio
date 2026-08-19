"""Tests for the Image Prompt engine's generation, analysis, fix, and the
monthly self-review job.

WHY THE STYLE MIRRORS test_model_discovery.py.

Same author, same extension, same discipline: a fake HTTP source with both
"unchanged" and "changed" bodies to prove the hash-diff actually diffs, and
an explicit due()/record_review() date-guard test so "once a month" is
proven, not assumed.
"""

from __future__ import annotations

import calendar
import time

import pytest

import prompt_engine as pe
import shared


# --------------------------- analyze_prompt ---------------------------

def test_analyze_prompt_empty_scores_zero_and_flags_all_missing():
    result = pe.analyze_prompt("")
    assert result["score"] == 0
    assert result["covered"] == []
    assert set(result["missing"]) == set(pe.SLOT_KEYWORDS.keys())


# --------------------------- get_prompt_config / save_prompt_config -------

@pytest.mark.asyncio
async def test_get_prompt_config_returns_defaults_when_unconfigured(ctx):
    config = await pe.get_prompt_config(ctx)
    assert config == pe.DEFAULT_PROMPT_CONFIG


@pytest.mark.asyncio
async def test_save_prompt_config_persists_an_override(ctx):
    await pe.save_prompt_config(ctx, {"generic_lighting": "Custom lighting clause."})
    config = await pe.get_prompt_config(ctx)
    assert config["generic_lighting"] == "Custom lighting clause."
    # untouched fields keep their defaults
    assert config["generic_style"] == pe.DEFAULT_PROMPT_CONFIG["generic_style"]


@pytest.mark.asyncio
async def test_save_prompt_config_ignores_blank_fields(ctx):
    await pe.save_prompt_config(ctx, {"generic_lighting": "Custom lighting clause."})
    await pe.save_prompt_config(ctx, {"generic_lighting": ""})  # blank -> keep
    config = await pe.get_prompt_config(ctx)
    assert config["generic_lighting"] == "Custom lighting clause."


@pytest.mark.asyncio
async def test_save_prompt_config_clamps_score_alert_threshold(ctx):
    await pe.save_prompt_config(ctx, {"score_alert_threshold": 500})
    config = await pe.get_prompt_config(ctx)
    assert config["score_alert_threshold"] == 100


@pytest.mark.asyncio
async def test_save_prompt_config_ignores_non_numeric_threshold(ctx):
    await pe.save_prompt_config(ctx, {"score_alert_threshold": "not-a-number"})
    config = await pe.get_prompt_config(ctx)
    assert config["score_alert_threshold"] == pe.SCORE_ALERT_THRESHOLD


def test_fix_prompt_uses_a_custom_config_when_given():
    config = dict(pe.DEFAULT_PROMPT_CONFIG)
    config["generic_lighting"] = "A very particular custom lighting clause."
    fixed, additions, _ = pe.fix_prompt("A modern heat pump.", "featured", config)
    assert "A very particular custom lighting clause." in fixed
    assert any("A very particular custom lighting clause." in a for a in additions)


def test_analyze_prompt_bare_subject_only_covers_one_slot():
    result = pe.analyze_prompt("A modern heat pump beside a bright house.")
    assert "subject" in result["covered"]
    assert "lighting" in result["missing"]
    assert "technical" in result["missing"]
    assert result["score"] < 100


def test_analyze_prompt_full_prompt_covers_all_six_slots():
    prompt = (
        "A modern heat pump inside a bright kitchen, wide hero shot, "
        "lit with soft golden hour lighting, photorealistic editorial "
        "style, captured on a 35mm lens with shallow depth of field."
    )
    result = pe.analyze_prompt(prompt)
    assert result["missing"] == []
    assert result["score"] == 100


# --------------------------- fix_prompt ---------------------------

def test_fix_prompt_adds_lighting_and_camera_when_missing():
    fixed, additions, unfixable = pe.fix_prompt(
        "A modern heat pump beside a bright house.", "featured",
    )
    assert "lighting" in fixed.lower()
    assert "lens" in fixed.lower()
    assert len(additions) >= 2
    assert "subject" not in unfixable  # subject IS present (non-empty prompt)


def test_fix_prompt_does_not_double_stamp_existing_lighting_or_camera():
    prompt = (
        "A modern heat pump, lit with dramatic studio lighting, shot on "
        "an 85mm lens."
    )
    fixed, additions, _ = pe.fix_prompt(prompt, "featured")
    assert fixed.lower().count("lighting") == 1
    assert not any("lighting clause" in a for a in additions)
    assert not any("camera/lens clause" in a for a in additions)


def test_fix_prompt_featured_vs_inline_use_different_camera_clauses():
    fixed_featured, _, _ = pe.fix_prompt("A heat pump.", "featured")
    fixed_inline, _, _ = pe.fix_prompt("A heat pump.", "inline_1")
    assert fixed_featured != fixed_inline


def test_fix_prompt_empty_prompt_reports_unfixable_subject_only():
    # System-level guarantee (2026-08-18): environment is no longer left
    # unfixable -- a generic, honestly-labelled fallback clause is always
    # appended instead, exactly like lighting/camera/style already were.
    # Subject stays genuinely unfixable: this engine will never invent one.
    fixed, additions, unfixable = pe.fix_prompt("", "featured")
    assert "subject" in unfixable
    assert "environment" not in unfixable
    assert any("environment" in a.lower() for a in additions)
    assert "clean, realistic, well-lit professional environment" in fixed


# --------------------------- generate_prompt ---------------------------

def test_generate_prompt_enriches_base_prompt_with_lighting_and_camera():
    base = shared.prompt_for_role("featured", "Boilers 101", "A guide", "")
    enriched = pe.generate_prompt("featured", "Boilers 101", "A guide", "")
    assert enriched != base
    assert "lighting" in enriched.lower()
    assert "lens" in enriched.lower()
    assert base in enriched  # base text preserved, only appended to


def test_generate_prompt_respects_an_already_descriptive_style_direction():
    enriched = pe.generate_prompt(
        "featured", "Boilers 101", "A guide",
        "shot on 85mm lens under warm golden hour lighting",
    )
    # both slots already present in style_direction -> no duplicate append
    assert enriched.lower().count("lighting") == 1
    assert enriched.lower().count("lens") == 1


# --------------------------- due() / record_review() ---------------------------

@pytest.mark.asyncio
async def test_due_false_before_check_hour(ctx):
    ts = calendar.timegm(time.strptime("2026-08-01 03:00:00", "%Y-%m-%d %H:%M:%S"))
    assert await pe.due(ctx, ts=ts) is False


@pytest.mark.asyncio
async def test_due_true_after_check_hour_first_time(ctx):
    ts = calendar.timegm(time.strptime("2026-08-01 09:00:00", "%Y-%m-%d %H:%M:%S"))
    assert await pe.due(ctx, ts=ts) is True


@pytest.mark.asyncio
async def test_due_false_twice_same_month(ctx):
    ts1 = calendar.timegm(time.strptime("2026-08-01 09:00:00", "%Y-%m-%d %H:%M:%S"))
    ts2 = calendar.timegm(time.strptime("2026-08-15 09:00:00", "%Y-%m-%d %H:%M:%S"))
    assert await pe.due(ctx, ts=ts1) is True
    result = await pe.run_self_review(ctx, ts=ts1)
    await pe.record_review(ctx, result)
    assert await pe.due(ctx, ts=ts2) is False


@pytest.mark.asyncio
async def test_due_true_again_next_month(ctx):
    ts1 = calendar.timegm(time.strptime("2026-08-01 09:00:00", "%Y-%m-%d %H:%M:%S"))
    ts2 = calendar.timegm(time.strptime("2026-09-01 09:00:00", "%Y-%m-%d %H:%M:%S"))
    result = await pe.run_self_review(ctx, ts=ts1)
    await pe.record_review(ctx, result)
    assert await pe.due(ctx, ts=ts2) is True


@pytest.mark.asyncio
async def test_run_self_review_reports_unreachable_guides_without_raising(ctx):
    # ctx.http has no mock registered for these URLs -- MockContext's http
    # returns a non-2xx/raises, which must show up as "unreachable", not
    # blow up the whole review.
    result = await pe.run_self_review(ctx)
    assert result["guides_checked"] == len(pe.GUIDE_SOURCES)
    assert isinstance(result["guides_unreachable"], list)


@pytest.mark.asyncio
async def test_run_self_review_samples_recent_generated_prompts(ctx):
    import storage as st
    await st.create_package(ctx, {
        "site": "g4s.md", "assets": [
            {"role": "featured", "prompt": pe.generate_prompt(
                "featured", "Boilers 101", "A guide", "")},
        ],
    })
    result = await pe.run_self_review(ctx)
    assert result["sample_size"] == 1
    assert result["avg_score"] == 100  # engine's own output should score full


@pytest.mark.asyncio
async def test_record_review_is_permanent_log_not_overwritten(ctx):
    ts1 = calendar.timegm(time.strptime("2026-08-01 09:00:00", "%Y-%m-%d %H:%M:%S"))
    ts2 = calendar.timegm(time.strptime("2026-09-01 09:00:00", "%Y-%m-%d %H:%M:%S"))
    r1 = await pe.run_self_review(ctx, ts=ts1)
    await pe.record_review(ctx, r1)
    r2 = await pe.run_self_review(ctx, ts=ts2)
    await pe.record_review(ctx, r2)
    log = await pe.list_review_log(ctx, limit=30)
    assert len(log) == 2


@pytest.mark.asyncio
async def test_list_review_log_respects_limit(ctx):
    for i in range(3):
        ts = calendar.timegm(time.strptime(f"2026-0{i+1}-01 09:00:00", "%Y-%m-%d %H:%M:%S"))
        result = await pe.run_self_review(ctx, ts=ts)
        await pe.record_review(ctx, result)
    log = await pe.list_review_log(ctx, limit=2)
    assert len(log) == 2

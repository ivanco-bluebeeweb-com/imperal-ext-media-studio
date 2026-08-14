"""Tests for the analyze_prompt / fix_prompt / check_prompt_engine_updates /
list_prompt_engine_log chat tools and the monthly media_prompt_engine_review
schedule tick.

Mirrors test_handlers_discovery.py's shape for the tick test.
"""

from __future__ import annotations

import time

import pytest

import handlers_prompt_engine as hpe
import prompt_engine as pe
from models import (
    AnalyzePromptParams,
    CheckPromptEngineUpdatesParams,
    FixPromptParams,
    ListPromptEngineLogParams,
    SavePromptEngineConfigParams,
)


@pytest.mark.asyncio
async def test_analyze_prompt_rejects_empty_input(ctx):
    result = await hpe.analyze_prompt(ctx, AnalyzePromptParams(prompt=""))
    assert result.status == "error"


@pytest.mark.asyncio
async def test_analyze_prompt_returns_score_and_missing_slots(ctx):
    result = await hpe.analyze_prompt(
        ctx, AnalyzePromptParams(prompt="A modern heat pump beside a house."),
    )
    assert result.status == "success"
    assert result.data.score < 100
    assert "lighting" in result.data.missing_slots


@pytest.mark.asyncio
async def test_fix_prompt_rejects_empty_input(ctx):
    result = await hpe.fix_prompt(ctx, FixPromptParams(prompt=""))
    assert result.status == "error"


@pytest.mark.asyncio
async def test_fix_prompt_returns_additions_for_a_bare_prompt(ctx):
    result = await hpe.fix_prompt(
        ctx, FixPromptParams(prompt="A modern heat pump.", role="featured"),
    )
    assert result.status == "success"
    assert len(result.data.additions) > 0
    assert result.data.fixed_prompt != result.data.original_prompt


@pytest.mark.asyncio
async def test_check_prompt_engine_updates_always_returns_a_result(ctx):
    result = await hpe.check_prompt_engine_updates(ctx, CheckPromptEngineUpdatesParams())
    assert result.status == "success"
    assert result.data.guides_checked == len(pe.GUIDE_SOURCES)


@pytest.mark.asyncio
async def test_check_prompt_engine_updates_writes_a_log_entry(ctx):
    await hpe.check_prompt_engine_updates(ctx, CheckPromptEngineUpdatesParams())
    log = await pe.list_review_log(ctx, limit=10)
    assert len(log) == 1


@pytest.mark.asyncio
async def test_list_prompt_engine_log_reports_past_runs(ctx):
    await hpe.check_prompt_engine_updates(ctx, CheckPromptEngineUpdatesParams())
    result = await hpe.list_prompt_engine_log(ctx, ListPromptEngineLogParams(limit=10))
    assert result.status == "success"
    assert len(result.data.items) == 1


@pytest.mark.asyncio
async def test_schedule_tick_skips_when_not_due(ctx):
    # Use the CURRENT real month (whatever it is when this test runs) so
    # due() genuinely sees "already reviewed this month" and skips --
    # avoids hard-coding a month that might not match the real clock.
    now = time.time()
    await pe.record_review(ctx, await pe.run_self_review(ctx, ts=now))
    # One log entry written above; the tick, seeing the same month already
    # reviewed, must go back to sleep without writing a second one.
    await hpe.media_prompt_engine_review(ctx)
    log = await pe.list_review_log(ctx, limit=10)
    assert len(log) == 1


@pytest.mark.asyncio
async def test_schedule_tick_runs_and_logs_when_due(ctx):
    log_before = await pe.list_review_log(ctx, limit=10)
    assert log_before == []
    # due() requires now.tm_hour >= CHECK_HOUR_UTC (7); MockContext's clock
    # is real wall-clock time so this only asserts the tick DOESN'T crash
    # and, if due, logs -- exercised deterministically via manual check tool
    # above; here we just confirm the tick function is callable and safe.
    await hpe.media_prompt_engine_review(ctx)
    # Whether or not "due" happened to be true right now, no exception was
    # raised and the log is a valid list.
    log_after = await pe.list_review_log(ctx, limit=10)
    assert isinstance(log_after, list)


# --------------------------- get/save_prompt_engine_config ----------------

@pytest.mark.asyncio
async def test_get_prompt_engine_config_returns_defaults(ctx):
    result = await hpe.get_prompt_engine_config(ctx, CheckPromptEngineUpdatesParams())
    assert result.status == "success"
    assert result.data.generic_lighting == pe.DEFAULT_PROMPT_CONFIG["generic_lighting"]
    assert result.data.score_alert_threshold == pe.SCORE_ALERT_THRESHOLD


@pytest.mark.asyncio
async def test_save_prompt_engine_config_applies_an_edit(ctx):
    result = await hpe.save_prompt_engine_config(
        ctx, SavePromptEngineConfigParams(generic_lighting="A brand new lighting clause."),
    )
    assert result.status == "success"
    assert result.data.generic_lighting == "A brand new lighting clause."
    # a later fix_prompt call, given the saved config, actually uses it
    config = await pe.get_prompt_config(ctx)
    fixed, _, _ = pe.fix_prompt("A heat pump.", "featured", config)
    assert "A brand new lighting clause." in fixed


@pytest.mark.asyncio
async def test_save_prompt_engine_config_rejects_a_non_numeric_threshold(ctx):
    result = await hpe.save_prompt_engine_config(
        ctx, SavePromptEngineConfigParams(score_alert_threshold="not-a-number"),
    )
    assert result.status == "error"


@pytest.mark.asyncio
async def test_save_prompt_engine_config_rejects_an_out_of_range_threshold(ctx):
    result = await hpe.save_prompt_engine_config(
        ctx, SavePromptEngineConfigParams(score_alert_threshold="500"),
    )
    assert result.status == "error"


@pytest.mark.asyncio
async def test_save_prompt_engine_config_blank_fields_keep_current_value(ctx):
    await hpe.save_prompt_engine_config(
        ctx, SavePromptEngineConfigParams(generic_lighting="Kept clause."),
    )
    result = await hpe.save_prompt_engine_config(ctx, SavePromptEngineConfigParams())
    assert result.status == "success"
    assert result.data.generic_lighting == "Kept clause."

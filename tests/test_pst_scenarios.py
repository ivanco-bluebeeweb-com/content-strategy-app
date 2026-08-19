"""Plausible Scenario Tests (PST) -- Content Strategy Hub.

Method: Docs/session-notes/SCENARIO_TESTING_STANDARD.md. The existing
test_smoke.py (2710 lines) already covers 34/38 chat functions across all
5 branches. This file targets ONLY the 4 functions a coverage audit found
never called by any existing test:
  - discover_opportunities_from_search_console
  - list_briefs
  - update_brief_title
  - purge_site_pipeline_data
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from imperal_sdk.testing import MockContext

import main as m
from schemas import (
    CreateSiteProfileParams, ListBriefsParams,
    DiscoverOpportunitiesFromSearchConsoleParams,
    UpdateBriefTitleParams, PurgeSitePipelineDataParams,
)


async def _make_site(ctx, site_id="site-1"):
    return await m.create_site_profile(ctx, CreateSiteProfileParams(
        site_id=site_id, domain="example.com", brand="Example Co",
        target_languages=["en"],
    ))


# ── discover_opportunities_from_search_console ──────────────────────────

@pytest.mark.asyncio
async def test_error_gsc_discovery_site_not_found():
    ctx = MockContext()
    result = await m.discover_opportunities_from_search_console(
        ctx, DiscoverOpportunitiesFromSearchConsoleParams(site_id="ghost-site", limit=10))
    assert result.error is not None


@pytest.mark.asyncio
async def test_blocked_gsc_discovery_ipc_failure_surfaces_real_error(monkeypatch):
    """Search Console connector unreachable/not connected -- the real IPC
    exception must be surfaced to the user (retryable), never silently
    treated as zero signals."""
    ctx = MockContext()
    await _make_site(ctx)

    async def _boom(*args, **kwargs):
        raise RuntimeError("google-search-console-connector not installed")
    ctx.extensions.call = _boom

    result = await m.discover_opportunities_from_search_console(
        ctx, DiscoverOpportunitiesFromSearchConsoleParams(site_id="site-1", limit=10))
    assert result.error is not None
    assert result.retryable is True
    assert "RuntimeError" in result.error


@pytest.mark.asyncio
async def test_adversarial_gsc_discovery_empty_rows_rejected_not_silent():
    """Search Console returns zero query rows -- must fail loudly, not
    silently return an empty opportunity list (which would look like a
    successful-but-boring discovery instead of 'nothing to check yet')."""
    ctx = MockContext()
    await _make_site(ctx)

    async def _empty(*args, **kwargs):
        return {"rows": []}
    ctx.extensions.call = _empty

    result = await m.discover_opportunities_from_search_console(
        ctx, DiscoverOpportunitiesFromSearchConsoleParams(site_id="site-1", limit=10))
    assert result.error is not None
    assert result.retryable is False


@pytest.mark.asyncio
async def test_happy_gsc_discovery_maps_rows_and_delegates():
    """discover_opportunities (which this wrapper delegates to) requires an
    existing content audit before allowing discovery, to avoid duplicating
    topics/cannibalizing keywords -- seed one first, matching the existing
    suite's own _seed_audit-equivalent pattern."""
    ctx = MockContext()
    await _make_site(ctx)
    await ctx.store.create("content_audits", {"site_id": "site-1", "audited_at": "test-seed"})

    async def _rows(*args, **kwargs):
        return {"rows": [
            {"query": "best running shoes", "impressions": 500, "clicks": 20, "ctr": 4.0, "position": 8.5},
        ]}
    ctx.extensions.call = _rows

    result = await m.discover_opportunities_from_search_console(
        ctx, DiscoverOpportunitiesFromSearchConsoleParams(site_id="site-1", limit=10))
    assert result.error is None


# ── list_briefs ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_happy_list_briefs_empty_when_none_created():
    ctx = MockContext()
    result = await m.list_briefs(ctx, ListBriefsParams())
    assert result.error is None
    assert result.data.total == 0


@pytest.mark.asyncio
async def test_happy_list_briefs_filters_by_site_id():
    ctx = MockContext()
    await _make_site(ctx, "site-a")
    await _make_site(ctx, "site-b")
    await ctx.store.create("article_briefs", {
        "site_id": "site-a", "working_title": "A brief", "target_language": "en",
    })
    await ctx.store.create("article_briefs", {
        "site_id": "site-b", "working_title": "B brief", "target_language": "en",
    })
    result = await m.list_briefs(ctx, ListBriefsParams(site_id="site-a"))
    assert result.error is None
    assert result.data.total == 1
    assert result.data.items[0].site_id == "site-a"


# ── update_brief_title ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_error_update_brief_title_not_found():
    ctx = MockContext()
    result = await m.update_brief_title(
        ctx, UpdateBriefTitleParams(brief_id="ghost-brief", working_title="New Title"))
    assert result.error is not None


@pytest.mark.asyncio
async def test_error_update_brief_title_rejects_empty_string():
    ctx = MockContext()
    doc = await ctx.store.create("article_briefs", {
        "site_id": "site-1", "working_title": "Заголовок на русском", "target_language": "ro",
    })
    result = await m.update_brief_title(
        ctx, UpdateBriefTitleParams(brief_id=doc.id, working_title="   "))
    assert result.error is not None


@pytest.mark.asyncio
async def test_happy_update_brief_title_fixes_wrong_language_title():
    """The exact documented bug this function exists to fix: a second-
    language brief kept the first language's title until corrected."""
    ctx = MockContext()
    doc = await ctx.store.create("article_briefs", {
        "site_id": "site-1", "working_title": "Заголовок на русском", "target_language": "ro",
    })
    result = await m.update_brief_title(
        ctx, UpdateBriefTitleParams(brief_id=doc.id, working_title="Titlul corect în română"))
    assert result.error is None
    assert result.data.working_title == "Titlul corect în română"

    reread = await m.list_briefs(ctx, ListBriefsParams(site_id="site-1"))
    assert reread.data.items[0].working_title == "Titlul corect în română"


# ── purge_site_pipeline_data ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_error_purge_site_pipeline_data_site_not_found():
    ctx = MockContext()
    result = await m.purge_site_pipeline_data(
        ctx, PurgeSitePipelineDataParams(site_id="ghost-site"))
    assert result.error is not None


@pytest.mark.asyncio
async def test_happy_purge_site_pipeline_data_removes_only_that_site():
    ctx = MockContext()
    await _make_site(ctx, "site-keep")
    await _make_site(ctx, "site-purge")
    await ctx.store.create("opportunities", {"site_id": "site-purge", "query": "q1"})
    await ctx.store.create("opportunities", {"site_id": "site-keep", "query": "q2"})

    result = await m.purge_site_pipeline_data(
        ctx, PurgeSitePipelineDataParams(site_id="site-purge"))
    assert result.error is None
    assert result.data.opportunities_removed == 1

    remaining = await m.list_opportunities(ctx, __import__("schemas").ListOpportunitiesParams())
    assert remaining.data.total == 1
    assert remaining.data.items[0].site_id == "site-keep"


@pytest.mark.asyncio
async def test_adversarial_purge_site_pipeline_data_idempotent_on_empty_site():
    """Purging a site with zero pipeline records must succeed with all
    counts at zero, not error -- an empty pipeline is a valid state, not
    a failure."""
    ctx = MockContext()
    await _make_site(ctx, "site-empty")
    result = await m.purge_site_pipeline_data(
        ctx, PurgeSitePipelineDataParams(site_id="site-empty"))
    assert result.error is None
    assert result.data.opportunities_removed == 0

    # calling it again on the same (still-empty) site must remain equally clean
    again = await m.purge_site_pipeline_data(
        ctx, PurgeSitePipelineDataParams(site_id="site-empty"))
    assert again.error is None
